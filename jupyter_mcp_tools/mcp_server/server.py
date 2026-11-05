"""MCP server that exposes tools installed into the shared tools directory.

Tools are installed out-of-band by the admin UI (see ``admin_ui``). This
server polls the shared directory periodically and keeps the live MCP tool
list in sync, so newly-shared tools become available to connected clients
without restarting the server.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import json
import logging
from pathlib import Path
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..common import groups as group_store
from ..common import builtin_skills, registry, users
from ..common.config import load_config
from ..common.doc_skill import DocSkillManifest
from ..common.loader import load_entrypoint
from ..common.registry import AnyManifest

logger = logging.getLogger("jupyter_mcp_tools.server")

SYNC_INTERVAL_SECONDS = 5.0

# Per-tool markers telling an agent how a skill actually behaves, prepended
# to its MCP description so it's visible in `tools/list` without calling
# anything. These exist because the two skill kinds are genuinely different
# and a blanket "everything here is executable" would be a lie:
#
# - A code skill really runs Python on this server, with the server's own
#   permissions — so an agent's own sandboxed shell being blocked is not a
#   reason to give up.
# - A doc skill only reads text back. Nothing is executed, so it cannot
#   stand in for "run this script".
#
# (And a code skill built on `common.skill_results.files_result()` returns
# files for the *human* to run under their own account — the tool itself
# still executed, but the end result isn't a server-side action. That's
# documented in the instructions, not per-tool, since it's a property of a
# skill's output rather than a separate kind.)
EXEC_MARKER = "[exec]"
READ_MARKER = "[read]"


def _make_doc_skill_tool(shared_dir: Path, manifest: DocSkillManifest):
    """Build the callable registered for a doc skill (SKILL.md bundle).

    Progressive disclosure, same idea the Agent Skills format itself is
    built around: calling with no argument returns the main instructions
    plus a listing of the bundle's other files, so an agent isn't handed
    the whole bundle's content on every call — only once it decides (from
    the listing) that a specific file is actually relevant.
    """
    skill_dir = registry.tools_dir(shared_dir) / manifest.name

    def skill(file: str = "") -> str:
        """Call with no arguments for the skill's main instructions and a
        list of its other files; call with `file` set to one of those
        paths to read that file's content."""
        if not file:
            listing = "\n".join(f"- {f}" for f in manifest.files) or "(no additional files)"
            return f"{manifest.body}\n\n---\nFiles available in this skill (call again with file=\"<path>\" to read one):\n{listing}"

        if file not in manifest.files:
            available = ", ".join(manifest.files) or "(none)"
            return f"Error: '{file}' is not a file in this skill. Available files: {available}"

        target = skill_dir / file
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: '{file}' is not a text file and can't be returned by this tool."

    return skill


def _param_hint(shared_dir: Path, manifest: AnyManifest) -> str:
    """A short, human/agent-readable description of what `arguments` a
    grouped skill takes, shown in its group's listing since the skill
    itself has no individual tool entry (and therefore no individual
    JSON schema) for an agent to inspect — see `_make_group_tool`."""
    if isinstance(manifest, DocSkillManifest):
        return 'file: str = "" (optional — path to one of this skill\'s other files)'
    try:
        fn = load_entrypoint(shared_dir, manifest)
        sig = inspect.signature(fn)
    except Exception:
        return "(unknown — failed to load)"

    parts = []
    for pname, p in sig.parameters.items():
        ann = p.annotation if p.annotation is not inspect.Parameter.empty else "Any"
        ann_str = getattr(ann, "__name__", str(ann))
        if p.default is inspect.Parameter.empty:
            parts.append(f"{pname}: {ann_str}")
        else:
            parts.append(f"{pname}: {ann_str} = {p.default!r}")
    return ", ".join(parts) or "(no arguments)"


def _make_group_tool(shared_dir: Path, group_manifests: dict[str, AnyManifest]):
    """Dispatcher tool for one registered group (see common/groups.py).

    Per explicit request, grouping is meant to actually shrink what an
    agent reads in `tools/list`, not just tag descriptions — so the
    skills in a group are *not* individually registered; this one tool
    stands in for all of them. Calling it with no `skill` returns a
    compact listing (name + description + argument hint) of what's in
    the group; calling it with `skill="<name>"` and `arguments={...}`
    dispatches to that skill, passing `arguments` through as its keyword
    arguments (or, for a doc skill, reading `arguments["file"]` the same
    way its own direct tool would read a `file` parameter).

    The cost: a skill folded into a group loses the precise,
    type-hint-derived JSON schema its own standalone tool would have had
    (`arguments` here is just a generic object) — an explicit, accepted
    tradeoff for actually reducing tool-list size; see PLAN.md.
    """

    def call(skill: str = "", arguments: dict | None = None) -> str:
        """Call with no `skill` to list what's in this group. Call again
        with `skill` set to one of those names (and `arguments` set to
        that skill's own keyword arguments, if it takes any) to run it."""
        if not skill:
            lines = []
            for name, manifest in sorted(group_manifests.items()):
                # Marked individually: a group may mix executable code skills
                # with read-only doc skills, so the group tool itself can't
                # honestly carry one marker -- see the module-level markers above.
                marker = READ_MARKER if isinstance(manifest, DocSkillManifest) else EXEC_MARKER
                lines.append(f"- {marker} {name}: {manifest.description}")
                lines.append(f"    arguments: {_param_hint(shared_dir, manifest)}")
            listing = "\n".join(lines) or "(no skills)"
            return f"Skills in this group:\n{listing}\n\nCall again with skill=\"<name>\" (and arguments={{...}} if it takes any) to use one."

        manifest = group_manifests.get(skill)
        if manifest is None:
            available = ", ".join(sorted(group_manifests)) or "(none)"
            return f"Error: '{skill}' is not a skill in this group. Available: {available}"

        kwargs = arguments or {}
        if isinstance(manifest, DocSkillManifest):
            doc_tool = _make_doc_skill_tool(shared_dir, manifest)
            return doc_tool(file=kwargs.get("file", ""))

        try:
            fn = load_entrypoint(shared_dir, manifest)
            result = fn(**kwargs)
        except Exception as exc:
            return f"Error calling '{skill}': {exc}"
        return result if isinstance(result, str) else json.dumps(result)

    return call


def _fingerprints(app: FastMCP) -> dict[str, tuple]:
    """Per-app cache of what's currently registered.

    Deliberately attached to the app rather than kept module-global: two
    FastMCP instances in one process (tests, or a future multi-tenant setup)
    must not share tool state, and a stale global would make the second app
    believe a tool it never registered is already up to date.
    """
    cache = getattr(app, "_jupyter_mcp_tool_fingerprints", None)
    if cache is None:
        cache = {}
        app._jupyter_mcp_tool_fingerprints = cache
    return cache


def _source_stamp(shared_dir: Path, manifest: AnyManifest) -> tuple:
    """Cheap identity of the file(s) a code skill is loaded from.

    mtime+size, not a content hash: this runs every 5 seconds for every
    skill, and the point is only "did the file change since we loaded it".
    """
    if manifest.kind != "code":
        return ()
    module_path = registry.tools_dir(shared_dir) / manifest.name / f"{manifest.module_name}.py"
    try:
        stat = module_path.stat()
        return (str(module_path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (str(module_path), 0, 0)


def _tool_fingerprint(shared_dir: Path, manifest: AnyManifest, description: str) -> tuple:
    """What must change for a tool to need re-registering."""
    return (
        manifest.name,
        description,
        manifest.kind,
        _source_stamp(shared_dir, manifest),
        tuple(getattr(manifest, "files", ()) or ()),
    )


def sync_tools(app: FastMCP, shared_dir: Path) -> None:
    """Reconcile the live FastMCP tool registry with the shared directory.

    A skill is registered as its own standalone tool unless its `group`
    matches a group actually created in the admin UI (see
    `common/groups.py`) — in which case it's folded into that group's
    one dispatcher tool instead (see `_make_group_tool`). An unrecognized
    group name (set on the skill but never created as a real group)
    falls back to standalone registration, same as having no group —
    it still gets a `[group] ` description prefix, just not hidden.
    """
    # `FastMCP.instructions` is read-only (it just proxies to
    # `_mcp_server.instructions`), so there's no public setter — but the
    # low-level attribute is a plain mutable string, which
    # `create_initialization_options()` reads fresh, not once at startup
    # — no restart needed for an edit to reach new connections. One
    # real wrinkle: a session's own `instructions` snapshot is taken
    # *before* that same session's lifespan (which is what calls
    # `sync_tools`, including this very line) has run, so a connection
    # already in flight at the moment of the edit won't see it — only
    # the connection *after* that one will, once some session's lifespan
    # has had a chance to refresh this attribute first.
    app._mcp_server.instructions = load_config().effective_instructions

    # Restore any missing built-in skill before scanning. Cheap (a stat per
    # built-in) and idempotent, and it's what makes them self-healing if the
    # folder is deleted underneath us.
    builtin_skills.ensure_builtin_skills(shared_dir)

    manifests = {m.name: m for m in registry.list_tools(shared_dir)}
    real_groups = {g.name: g for g in group_store.list_groups(shared_dir)}

    grouped: dict[str, dict[str, AnyManifest]] = {}
    standalone: dict[str, AnyManifest] = {}
    for name, manifest in manifests.items():
        if manifest.group and manifest.group in real_groups:
            grouped.setdefault(manifest.group, {})[name] = manifest
        else:
            standalone[name] = manifest

    desired_names = set(standalone) | {f"group_{g}" for g in grouped}
    existing_names = {t.name for t in app._tool_manager.list_tools()}

    for name in existing_names - desired_names:
        # "Couldn't read it this tick" is not "it was deleted". A skill is
        # briefly unreadable whenever its files are being written — an install
        # through the admin UI, `pip install`, a `git pull`, or an antivirus
        # holding the file. Unregistering on that makes the tool vanish from
        # `tools/list` for up to a tick, which an agent reports as "tool not
        # available". Only a folder that is genuinely gone justifies removal.
        if registry.tools_dir(shared_dir).joinpath(name).exists():
            logger.warning(
                "tool '%s' could not be read this tick -- keeping it registered", name
            )
            continue
        app.remove_tool(name)
        _fingerprints(app).pop(name, None)
        logger.info("unregistered tool removed from shared dir: %s", name)

    for name, manifest in standalone.items():
        if isinstance(manifest, DocSkillManifest):
            entrypoint = _make_doc_skill_tool(shared_dir, manifest)
        else:
            try:
                entrypoint = load_entrypoint(shared_dir, manifest)
            except Exception:
                logger.exception("failed to load tool '%s', skipping", name)
                continue

        prefix = f"[{manifest.group}] " if manifest.group else ""
        # The marker goes first, before the group prefix: agents read the
        # description from the front, and "does this actually run anything"
        # is the more consequential fact than which category it belongs to.
        marker = READ_MARKER if isinstance(manifest, DocSkillManifest) else EXEC_MARKER
        description = f"{marker} {prefix}{manifest.description}"

        # Re-register only when something about the tool actually changed.
        # This used to re-register unconditionally "so edits take effect",
        # which meant every 5-second tick tore down and rebuilt every tool --
        # each one a log line, on every connected session, forever. Fine for
        # three skills, awful for thirty, and it made the log useless for
        # spotting real changes.
        fingerprint = _tool_fingerprint(shared_dir, manifest, description)
        if _fingerprints(app).get(name) == fingerprint:
            continue

        if name in existing_names:
            app.remove_tool(name)
        app.add_tool(entrypoint, name=manifest.name, description=description)
        _fingerprints(app)[name] = fingerprint
        logger.info("registered tool: %s (group=%r)", manifest.name, manifest.group)

    for group_name, group_manifests in grouped.items():
        tool_name = f"group_{group_name}"
        if tool_name in existing_names:
            app.remove_tool(tool_name)
        # A group can mix both kinds, so it gets no single marker — its own
        # listing marks each skill individually instead (see _make_group_tool).
        description = real_groups[group_name].description
        fingerprint = (
            "group",
            description,
            tuple(sorted(group_manifests)),
        )
        if _fingerprints(app).get(tool_name) == fingerprint:
            continue
        app.add_tool(
            _make_group_tool(shared_dir, group_manifests),
            name=tool_name,
            description=description,
        )
        _fingerprints(app)[tool_name] = fingerprint
        logger.info("registered group tool: %s (%d skills)", tool_name, len(group_manifests))


class LogMCPUserMiddleware(BaseHTTPMiddleware):
    """Records who's calling, for the admin UI's "Users" list.

    Only meaningful over the streamable-http transport, where a client can
    set a header; stdio transport has no notion of a remote caller
    identity (it's a local subprocess per MCP client), so there's nothing
    to log there — see PLAN.md.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        username = request.headers.get("x-mcp-user")
        if username:
            try:
                users.record_user(username)
            except Exception:
                logger.exception("failed to record MCP caller '%s'", username)
        return await call_next(request)


async def _tool_sync_loop(app: FastMCP, shared_dir: Path) -> None:
    while True:
        try:
            sync_tools(app, shared_dir)
        except Exception:
            logger.exception("error while syncing tools from %s", shared_dir)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


def build_app(
    shared_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    poll: bool = True,
) -> FastMCP:
    """Build the MCP app.

    `poll=False` syncs once at startup and never again. That's the right mode
    for stdio: the process lives exactly as long as one client session (measured
    ~0.9s for a trivial session), so every tick would re-read a directory that
    cannot have changed — the next session reads it fresh anyway. Polling also
    costs something: a tick that fails to read a skill (file being written, an
    antivirus holding it, a network filesystem hiccup) is indistinguishable
    from a deleted skill and unregisters it, which an agent sees as "tool not
    available" for up to 5 seconds. HTTP mode keeps polling, because there one
    long-lived process serves many clients and an admin's install must appear
    without a restart.
    """

    @contextlib.asynccontextmanager
    async def lifespan(app: FastMCP) -> AsyncIterator[dict]:
        sync_tools(app, shared_dir)
        task = asyncio.create_task(_tool_sync_loop(app, shared_dir)) if poll else None
        try:
            yield {}
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    # `host` must be passed in at construction time, not set on
    # `app.settings` afterwards: FastMCP auto-enables DNS-rebinding
    # protection (locking Host-header validation to 127.0.0.1/localhost
    # patterns) *during __init__*, based on whatever `host` it's given
    # right then — setting app.settings.host later doesn't undo that.
    # Binding 0.0.0.0 to serve through a reverse proxy/JupyterHub (whose
    # Host header is whatever external domain the client used, not
    # 127.0.0.1) was getting every request rejected with a misleading
    # "421 Misdirected Request" because of this — see PLAN.md.
    return FastMCP(
        "jupyter-mcp-tools",
        # Just the startup value — sync_tools() (called once immediately
        # by `lifespan` above, then every SYNC_INTERVAL_SECONDS) re-reads
        # this from config on every tick, so an admin edit to it takes
        # effect live without restarting this process.
        instructions=load_config().effective_instructions,
        lifespan=lifespan,
        host=host,
        port=port,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="jupyter-mcp-tools MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport to serve (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--shared-dir",
        default=None,
        help="Override the shared tools directory configured for the admin UI",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    config = load_config()
    shared_dir = Path(args.shared_dir) if args.shared_dir else config.shared_dir_path
    shared_dir.mkdir(parents=True, exist_ok=True)
    logger.info("serving tools from %s", shared_dir)

    # Both network transports serve many clients from one process, so the
    # directory has to be re-scanned while it runs. stdio is one process per
    # session and re-reads at every start, so polling only adds failure modes.
    if args.transport in ("sse", "streamable-http"):
        app = build_app(shared_dir, host=args.host, port=args.port, poll=True)
    else:
        app = build_app(shared_dir, poll=False)

    if args.transport == "streamable-http":
        # Bypass app.run() here so we can attach LogMCPUserMiddleware to the
        # underlying Starlette app before serving it.
        import uvicorn

        starlette_app = app.streamable_http_app()
        starlette_app.add_middleware(LogMCPUserMiddleware)
        uvicorn.run(starlette_app, host=args.host, port=args.port, log_level="info")
    else:
        app.run(transport=args.transport)


if __name__ == "__main__":
    main()

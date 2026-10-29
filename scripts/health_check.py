#!/usr/bin/env python3
"""End-to-end check of a running deployment: is it actually serving?

Judges the deployment the way a user and an agent see it, not the way the
code sees itself — no route exists that says "everything is fine", so this
exercises the real HTTP/MCP surface instead:

1. The admin UI answers, and its auth gate still rejects anonymous access
   (a page that returns 200 where it should redirect is a silent regression:
   the app "works" while being open to anyone who can reach the port).
2. The MCP server completes a real handshake and lists tools.
3. A tool can actually be *called*, not merely listed — listing exercises
   the registry, calling exercises loading and executing the skill module.
4. The tools on disk match the tools served, catching the common "I dropped
   a skill into the directory but the server never picked it up" case
   (which is a 5-second poll, so a mismatch right after a copy is expected
   and this reports it as a warning, not a failure).

Exit code is 0 only when everything critical passed, so it can be used in
a cron job or a deploy step.

Usage:
    python scripts/health_check.py
    python scripts/health_check.py --mcp-url http://host:8765/mcp --admin-url http://host:8766
    python scripts/health_check.py --skip-admin        # MCP only
    python scripts/health_check.py --call echo         # also call this tool
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path

PASS = "OK  "
FAIL = "FAIL"
WARN = "WARN"


class Report:
    def __init__(self) -> None:
        self.failed = 0

    def ok(self, message: str) -> None:
        print(f"[{PASS}] {message}")

    def fail(self, message: str) -> None:
        print(f"[{FAIL}] {message}")
        self.failed += 1

    def warn(self, message: str) -> None:
        print(f"[{WARN}] {message}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):  # type: ignore[misc]
    """A redirect *is* the expected answer for gated pages.

    Following it would hide the difference between "the gate redirects"
    and "the page is open to anyone", which is the thing being checked.
    """

    def redirect_request(self, *args, **kwargs):  # noqa: D102
        return None


def _http_get(url: str, *, timeout: float = 10.0, auth: tuple[str, str] | None = None):
    request = urllib.request.Request(url, method="GET")
    if auth:
        raw = f"{auth[0]}:{auth[1]}".encode()
        request.add_header("Authorization", "Basic " + base64.b64encode(raw).decode())
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        # Connection refused / DNS failure / timeout. Normalized to status 0
        # so every caller handles "not listening" the same way instead of
        # each one needing its own try/except.
        return 0, str(exc.reason).encode()
    except TimeoutError:
        return 0, b"timeout"


def check_shared_dir(report: Report, shared_dir: Path | None) -> None:
    """Independent of whether the UI is up — it's a filesystem check."""
    if shared_dir is None:
        return
    tools_dir = shared_dir / "tools"
    if tools_dir.is_dir():
        names = sorted(p.name for p in tools_dir.iterdir() if p.is_dir())
        report.ok(f"shared dir readable: {len(names)} skill(s) on disk in {tools_dir}")
    else:
        report.fail(f"shared dir has no tools/ subdirectory: {tools_dir}")


def check_admin_ui(report: Report, base: str) -> None:
    base = base.rstrip("/")

    status, body = _http_get(f"{base}/login")
    if status == 200:
        report.ok(f"admin UI reachable: GET {base}/login -> 200")
    elif status == 0:
        report.fail(
            f"admin UI is not listening on {base} ({body.decode(errors='replace')}) — "
            "is the process running?"
        )
        return
    else:
        report.fail(f"admin UI: GET {base}/login -> {status}")

    # The auth gate. /docs is representative: every non-root protected page
    # must redirect to the login page rather than render.
    status, _ = _http_get(f"{base}/docs")
    if status in (303, 302, 307):
        report.ok(f"auth gate active: GET {base}/docs -> {status} (redirects to login)")
    elif status == 200:
        report.fail(
            f"auth gate NOT active: GET {base}/docs -> 200 — protected pages "
            "render for anonymous visitors"
        )
    else:
        report.fail(f"GET {base}/docs -> {status} (expected a redirect to login)")
    # The machine API must reject anonymous callers too, and must not
    # accept wrong credentials.
    status, _ = _http_get(f"{base}/api/tools")
    if status == 401:
        report.ok(f"JSON API rejects anonymous access: GET {base}/api/tools -> 401")
    else:
        report.fail(f"GET {base}/api/tools -> {status} (expected 401)")

    status, _ = _http_get(f"{base}/api/tools", auth=("definitely", "wrong"))
    if status == 401:
        report.ok("JSON API rejects wrong credentials: -> 401")
    else:
        report.fail(f"wrong credentials were not rejected: -> {status} (expected 401)")


async def check_mcp(report: Report, url: str, call: str | None) -> set[str]:
    """Handshake, list, optionally call. Returns the served tool names."""
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError:
        report.fail(
            "the `mcp` package is not importable — run this inside the project venv "
            "(e.g. .venv/bin/python scripts/health_check.py)"
        )
        return set()

    served: set[str] = set()
    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                report.ok(
                    f"MCP handshake: {url} -> "
                    f"{init.serverInfo.name} {init.serverInfo.version}"
                )
                tools = await session.list_tools()
                served = {t.name for t in tools.tools}
                if served:
                    report.ok(f"tools/list -> {len(served)} tool(s): {', '.join(sorted(served))}")
                else:
                    # Legitimate (a fresh deployment may have no skills yet)
                    # but never what someone running a health check expects.
                    report.warn(
                        "tools/list is empty — no skills installed in the shared dir "
                        "(the server is up, it just has nothing to offer)"
                    )
                if call:
                    if call not in served:
                        report.fail(f"cannot call {call!r}: not in tools/list")
                    else:
                        result = await session.call_tool(call, {})
                        text = ""
                        for item in result.content:
                            text += getattr(item, "text", "")
                        if result.isError:
                            report.fail(f"call_tool({call!r}) returned an error: {text[:300]}")
                        else:
                            preview = " ".join(text.split())[:80]
                            report.ok(f"call_tool({call!r}) executed: {preview!r}")
    except Exception as exc:  # noqa: BLE001 - network/SDK errors are all "down"
        # AnyIO wraps connection failures in a TaskGroup exception whose
        # str() is just "unhandled errors in a TaskGroup" — useless for
        # diagnosis. Unwrap to the first real cause (usually
        # "Connection refused"), which is what the operator needs to see.
        cause = _root_cause(exc)
        report.fail(f"MCP check failed for {url}: {cause}")
        if "-v" in sys.argv or "--verbose" in sys.argv:
            traceback.print_exc()
    return served


def _root_cause(exc: BaseException) -> str:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        nested = getattr(current, "exceptions", None)  # ExceptionGroup
        if nested:
            current = nested[0]
            continue
        if current.__cause__ is not None:
            current = current.__cause__
            continue
        if current.__context__ is not None and not getattr(current, "exceptions", None):
            current = current.__context__
            continue
        break
    text = str(current) if current is not None else str(exc)
    return text or type(exc).__name__


def compare_disk_and_served(report: Report, shared_dir: Path | None, served: set[str]) -> None:
    if shared_dir is None or not served:
        return
    tools_dir = shared_dir / "tools"
    if not tools_dir.is_dir():
        return
    on_disk = {p.name for p in tools_dir.iterdir() if p.is_dir()}
    missing = on_disk - served
    if missing:
        # Grouped skills are hidden behind their group's dispatcher tool, so a
        # skill absent from tools/list is often correct. Anything not found
        # under either its own name or group_<name> is worth flagging.
        suspicious = {n for n in missing if f"group_{n}" not in served}
        if suspicious:
            report.warn(
                f"{len(suspicious)} skill(s) on disk but not served (grouped, or an "
                f"invalid skill): {', '.join(sorted(suspicious))}"
            )
        else:
            report.ok(f"{len(missing)} skill(s) served through a group dispatcher")
    extra = served - on_disk - {f"group_{n}" for n in on_disk}
    if extra:
        report.warn(f"served but not on disk: {', '.join(sorted(extra))}")


def load_shared_dir() -> Path | None:
    try:
        from jupyter_mcp_tools.common.config import load_config

        return load_config().shared_dir_path
    except Exception:  # noqa: BLE001 - the check must run outside the project too
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-url", default="http://127.0.0.1:8766")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--skip-admin", action="store_true", help="check only the MCP server")
    parser.add_argument("--skip-mcp", action="store_true", help="check only the admin UI")
    parser.add_argument("--call", metavar="TOOL", help="also call this tool after listing")
    parser.add_argument(
        "--shared-dir",
        help="shared tools dir to compare against disk (default: read from config)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    report = Report()

    shared_dir: Path | None
    if args.shared_dir:
        shared_dir = Path(args.shared_dir).expanduser()
    else:
        shared_dir = load_shared_dir()

    if not args.skip_admin:
        print("== admin UI ==")
        check_admin_ui(report, args.admin_url)
        check_shared_dir(report, shared_dir)
        print()

    served: set[str] = set()
    if not args.skip_mcp:
        print("== MCP server ==")
        served = asyncio.run(check_mcp(report, args.mcp_url, args.call))
        compare_disk_and_served(report, shared_dir, served)
        print()

    if report.failed:
        print(f"{report.failed} check(s) failed.")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
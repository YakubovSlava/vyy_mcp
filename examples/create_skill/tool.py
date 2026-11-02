"""Meta-skill: lets an agent create a new skill directly in the shared
skills directory that this MCP server is already watching, without going
through the admin UI. Writes straight to disk; the next background sync
(a few seconds) picks it up and it shows up in tools/list.

Every candidate is **verified before it's installed**, not just structurally
validated: a code skill is actually imported in a throwaway subprocess (see
`common/skill_verify.py`), so a module that parses fine but blows up on
import — the classic `from lib.util import ...` case — is rejected here
instead of failing later when someone calls the tool. Use `verify_only=True`
to dry-run a candidate without touching the shared directory.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from jupyter_mcp_tools.common import registry, skill_verify
from jupyter_mcp_tools.common.config import load_config
from jupyter_mcp_tools.common.manifest import ToolValidationError, validate_dir
from jupyter_mcp_tools.common.registry import ToolAlreadyExistsError


def create_skill(
    kind: str,
    name: str = "",
    description: str = "",
    body: str = "",
    source: str = "",
    group: str = "",
    overwrite: bool = False,
    verify_only: bool = False,
) -> str:
    """Creates a new skill directly in the shared skills directory this MCP server watches.

    For kind="doc": pass `name`, `description`, and `body` (the SKILL.md
    markdown text, no frontmatter needed). `group` is optional — set it
    to an existing group's name (see the "Группы" admin tab) to fold this
    skill behind that group's dispatcher tool instead of listing it on
    its own.

    For kind="code": pass `source` — the full content of a single `.py`
    file with exactly one top-level function, which must have a
    docstring (becomes the skill's description) and a name matching
    [a-z0-9][a-z0-9_-]{1,63} (becomes the skill's name). Its parameters'
    type hints become the MCP input schema automatically. An optional
    module-level `__group__ = "..."` string works the same way as
    `group` above. `name`/`description`/`group` arguments are ignored
    for this kind — exactly like uploading a code skill through the
    admin UI, everything comes from the function/module itself.

    Both kinds are verified before anything is written: the result names
    the skill, its final signature, and any problems found. A code skill
    that fails to import, hangs on import, or has no docstring is
    rejected with an explanation instead of being installed.

    Set verify_only=True to run that verification and change nothing —
    useful for checking a candidate before committing to it.

    Set overwrite=True to replace an existing skill with the same name;
    otherwise an already-installed skill with that name raises an error.
    Returns a short confirmation naming the skill that was installed.
    """
    if kind not in ("doc", "code"):
        raise ValueError('kind must be "doc" or "code"')

    shared_dir = load_config().shared_dir_path

    if kind == "doc":
        verdict = skill_verify.verify_doc_bundle(body, name=name, description=description)
        if not verdict.ok:
            return f"Не установлено. {verdict.summary()}"
        if verify_only:
            return f"Проверка пройдена, ничего не изменено.\n{verdict.summary()}"

        manifest = registry.create_doc_skill(
            shared_dir, name=name, description=description, body=body, group=group, overwrite=overwrite
        )
        return f"{verdict.summary()}\nСоздан doc skill '{manifest.name}'. Появится в tools/list после следующей синхронизации."

    if not source.strip():
        raise ValueError("source must contain the full content of the skill's .py file")

    # 1. Verify by actually importing, in a subprocess. This is what catches
    #    modules that parse but can't load — the failure a pure AST check
    #    misses.
    verdict = skill_verify.verify_code_source(source)
    if not verdict.ok:
        return f"Не установлено. {verdict.summary()}"

    # 2. Only then the structural check that produces the manifest (name,
    #    docstring, __group__) — keeps a single source of truth for what
    #    gets written.
    tools_dir = registry.tools_dir(shared_dir)
    if verify_only:
        return f"Проверка пройдена, ничего не изменено.\n{verdict.summary()}"

    tools_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=tools_dir, prefix=".staging-"))
    try:
        (staging / "tool.py").write_text(source, encoding="utf-8")
        manifest = validate_dir(staging)
    except ToolValidationError:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    dest = tools_dir / manifest.name
    if dest.exists():
        if not overwrite:
            shutil.rmtree(staging, ignore_errors=True)
            raise ToolAlreadyExistsError(
                f"skill '{manifest.name}' already exists; pass overwrite=True to replace it"
            )
        shutil.rmtree(dest)
    shutil.move(str(staging), str(dest))
    return f"{verdict.summary()}\nСоздан code skill '{manifest.name}'. Появится в tools/list после следующей синхронизации."

"""Meta-skill: lets an agent create a new skill directly in the shared
skills directory that this MCP server is already watching, without going
through the admin UI. Writes straight to disk; the next background sync
(a few seconds) picks it up and it shows up in tools/list.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from jupyter_mcp_tools.common import registry
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

    Set overwrite=True to replace an existing skill with the same name;
    otherwise an already-installed skill with that name raises an error.
    Returns a short confirmation naming the skill that was installed.
    """
    if kind not in ("doc", "code"):
        raise ValueError('kind must be "doc" or "code"')

    shared_dir = load_config().shared_dir_path

    if kind == "doc":
        manifest = registry.create_doc_skill(
            shared_dir, name=name, description=description, body=body, group=group, overwrite=overwrite
        )
        return f"Created doc skill '{manifest.name}'. It will show up in tools/list after the next sync."

    if not source.strip():
        raise ValueError("source must contain the full content of the skill's .py file")

    tools_dir = registry.tools_dir(shared_dir)
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
    return f"Created code skill '{manifest.name}'. It will show up in tools/list after the next sync."

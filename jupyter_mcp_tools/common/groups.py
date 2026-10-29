"""Skill groups: named categories with their own description, stored
alongside the shared tools directory (`<shared_dir>/groups.json`).

A group exists to do two things at once, per explicit request:

1. Let the admin UI search/filter skills by category.
2. Actually reduce what a connecting agent has to read to find a
   relevant tool — unlike a skill's free-text `group` field tag (which
   is visible but doesn't shrink `tools/list`), skills assigned to a
   *registered* group are hidden from `tools/list` individually and
   folded behind one dispatcher tool per group (see
   `mcp_server/server.py`'s `sync_tools()`), whose own MCP description
   is the group's description here. An agent reads one short group
   description instead of N full skill descriptions to decide whether
   anything in that group is relevant at all.

A skill's `group` string (see `common/manifest.py`/`common/doc_skill.py`)
only takes effect this way if it matches a group created here by name —
an unrecognized group name falls back to registering that skill directly
as its own tool, same as having no group at all (see `sync_tools()`).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .manifest import NAME_RE, ToolValidationError

logger = logging.getLogger("jupyter_mcp_tools.groups")


def groups_path(shared_dir: Path) -> Path:
    return shared_dir / "groups.json"


@dataclass
class Group:
    name: str
    description: str


def _load(path: Path) -> dict[str, dict]:
    """Read the group store, tolerating a damaged file.

    This runs inside the MCP server's 5-second sync loop, so anything that
    raises here doesn't just fail one page render — it stops every tool
    from being registered, and the whole server goes dark. A hand-edited or
    truncated `groups.json` therefore degrades to "no groups" (every skill
    registers standalone, exactly as if no group existed) instead of taking
    the server down. Writes still go through `_save`, so the admin UI's own
    edits are unaffected.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("group store at %s is unreadable -- treating as empty", path)
        return {}
    if not isinstance(data, dict):
        logger.warning("group store at %s is not a JSON object -- treating as empty", path)
        return {}
    return {name: entry for name, entry in data.items() if isinstance(entry, dict)}


def _save(path: Path, data: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


class GroupAlreadyExistsError(ValueError):
    pass


class GroupNotFoundError(ValueError):
    pass


def list_groups(shared_dir: Path) -> list[Group]:
    data = _load(groups_path(shared_dir))
    groups = [Group(name=name, description=str(entry.get("description", ""))) for name, entry in data.items()]
    groups.sort(key=lambda g: g.name)
    return groups


def get_group(shared_dir: Path, name: str) -> Group | None:
    data = _load(groups_path(shared_dir))
    entry = data.get(name)
    return Group(name=name, description=str(entry.get("description", ""))) if entry else None


def create_group(shared_dir: Path, name: str, description: str, overwrite: bool = False) -> Group:
    if not name or not NAME_RE.match(name):
        raise ToolValidationError("group name must match [a-z0-9][a-z0-9_-]{1,63}")
    if not description:
        raise ToolValidationError("group needs a description")

    path = groups_path(shared_dir)
    data = _load(path)
    if name in data and not overwrite:
        raise GroupAlreadyExistsError(f"group '{name}' already exists; set overwrite=True to replace it")

    data[name] = {"description": description}
    _save(path, data)
    return Group(name=name, description=description)


def delete_group(shared_dir: Path, name: str) -> None:
    path = groups_path(shared_dir)
    data = _load(path)
    if name not in data:
        raise GroupNotFoundError(f"group '{name}' does not exist")
    del data[name]
    _save(path, data)

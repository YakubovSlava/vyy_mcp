"""Tracking for MCP end users (distinct from the admins in `common/auth.py`).

An end user never gets a managed admin account. Instead, two independent
paths upsert the same flat JSON store by username:

1. The distributed connection notebook (see `notebooks/connect_to_mcp.ipynb`,
   built by `admin_ui/notebook_builder.py`) self-registers by POSTing
   `getpass.getuser()` to `/api/users/register` over `curl`, right after
   printing the MCP connection command.
2. The MCP server itself upserts by username on every call it receives
   (see `mcp_server/server.py`'s `LogMCPUserMiddleware`), covering users
   who connect without ever running the notebook.

This is a display/tracking feature, not an access-control boundary —
`/api/users/register` is intentionally open to anyone who can reach the
main host (see PLAN.md).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


def users_path() -> Path:
    override = os.environ.get("MCP_TOOLS_USERS_PATH")
    if override:
        return Path(override)
    return Path.home() / ".jupyter_mcp_tools" / "users.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class UserRecord:
    username: str
    first_seen: str
    last_seen: str


def _load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save(path: Path, data: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def record_user(username: str) -> None:
    """Upsert a sighting of `username` — called by both the notebook's
    self-registration request and the MCP server's per-call logging."""
    path = users_path()
    data = _load(path)

    if username in data:
        data[username]["last_seen"] = _now()
    else:
        now = _now()
        data[username] = asdict(UserRecord(username=username, first_seen=now, last_seen=now))

    _save(path, data)


def list_users() -> list[dict]:
    # Skip anything that doesn't match the current schema (e.g. leftover
    # entries from an older version of this store) rather than crashing
    # the whole list — same defensive approach as registry.list_tools.
    records = [r for r in _load(users_path()).values() if r.get("username") and r.get("last_seen")]
    records.sort(key=lambda r: r["last_seen"], reverse=True)
    return records

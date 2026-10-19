"""Admin credential store for the main-host admin UI.

The main admin (the one hosting the real shared tools directory) issues a
username/password to every other ("child") admin via the
`jupyter-mcp-tools-admin-users` CLI (see `admin_ui/manage_admins.py`).
Those credentials are checked with HTTP Basic Auth on every request to the
main host's admin UI/API — see `admin_ui/main.py`.

Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib `hashlib`, no extra
dependency) and a random per-user salt. This is deliberately simple, not a
general-purpose auth system: there's one flat set of admins, all equally
privileged, matching the "trusted internal operators" model the rest of
this project targets (see PLAN.md).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

PBKDF2_ITERATIONS = 390_000


def admins_path() -> Path:
    override = os.environ.get("MCP_TOOLS_ADMINS_PATH")
    if override:
        return Path(override)
    return Path.home() / ".jupyter_mcp_tools" / "admins.json"


@dataclass
class _Record:
    salt: str
    hash: str
    iterations: int = PBKDF2_ITERATIONS


def _hash_password(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations).hex()


def _load(path: Path) -> dict[str, _Record]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {username: _Record(**rec) for username, rec in raw.items()}


def _save(path: Path, records: dict[str, _Record]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({u: asdict(r) for u, r in records.items()}, indent=2))
    path.chmod(0o600)


def list_admins() -> list[str]:
    return sorted(_load(admins_path()).keys())


def add_admin(username: str, password: str) -> None:
    path = admins_path()
    records = _load(path)
    salt = secrets.token_bytes(16)
    records[username] = _Record(
        salt=salt.hex(),
        hash=_hash_password(password, salt),
    )
    _save(path, records)


def remove_admin(username: str) -> bool:
    path = admins_path()
    records = _load(path)
    if username not in records:
        return False
    del records[username]
    _save(path, records)
    return True


def verify_admin(username: str, password: str) -> bool:
    records = _load(admins_path())
    record = records.get(username)
    if record is None:
        return False
    salt = bytes.fromhex(record.salt)
    expected = _hash_password(password, salt, record.iterations)
    return secrets.compare_digest(expected, record.hash)


def has_any_admin() -> bool:
    return bool(_load(admins_path()))

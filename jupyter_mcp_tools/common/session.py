"""Signed session tokens for the admin UI's login page.

The browser-facing login uses a normal HTML form instead of the
browser's native HTTP Basic Auth popup, so the admin UI issues its own
signed, expiring cookie after checking credentials against
`common.auth`. No extra dependency (no itsdangerous/sessions middleware):
just HMAC-SHA256 over a username+expiry payload with a secret key
generated once and kept on disk next to `admins.json`.
"""
from __future__ import annotations

import base64
import hmac
import os
import time
from hashlib import sha256
from pathlib import Path

from . import paths

SESSION_TTL_SECONDS = 12 * 60 * 60
COOKIE_NAME = "mjapp_session"


def _secret_key_path() -> Path:
    override = os.environ.get("MCP_TOOLS_SESSION_SECRET_PATH")
    if override:
        return Path(override)
    return paths.data_dir() / "session_secret"


def _secret_key() -> bytes:
    path = _secret_key_path()
    if path.exists():
        return bytes.fromhex(path.read_text().strip())
    key = os.urandom(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key.hex())
    path.chmod(0o600)
    return key


def _sign(payload: str) -> str:
    return hmac.new(_secret_key(), payload.encode(), sha256).hexdigest()


def create_token(username: str, ttl: int = SESSION_TTL_SECONDS) -> str:
    expiry = int(time.time()) + ttl
    payload = f"{username}:{expiry}"
    raw = f"{payload}:{_sign(payload)}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def verify_token(token: str) -> str | None:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        username, expiry_str, sig = raw.rsplit(":", 2)
    except (ValueError, UnicodeDecodeError):
        return None

    if not hmac.compare_digest(_sign(f"{username}:{expiry_str}"), sig):
        return None
    if int(expiry_str) < int(time.time()):
        return None
    return username

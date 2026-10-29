"""Per-user launcher state: how this machine's copy of the server was set up.

Written once by `jupyter_mcp_tools.bootstrap` (the interactive "log in with
your repo credentials" step), read on every launch by
`jupyter_mcp_tools.launcher` (the thin non-interactive process an MCP client
spawns). Kept separate from `common/config.py` on purpose: that file is the
*admin's* config for a hosted deployment, this one is an *end user's* local
checkout (repo URL, cache dir, venv, corporate package index).

Two constraints shaped the shape of this file:

1. **It holds a repo token**, so it is written `0600` in the same
   `~/.jupyter_mcp_tools/` directory `config.json` already lives in, and is
   never placed in an MCP client's own config (`mcp.json`,
   `claude_desktop_config.json`) — those files get committed, copied, and
   backed up far more casually than this one.
2. **The launcher must stay silent on stdout.** stdout of the spawned
   process *is* the MCP JSON-RPC stream, so nothing in the launch path may
   print there — see `mcp_server` notes in PLAN.md. The token therefore
   travels to `git` through `GIT_CONFIG_*` environment variables rather
   than a `-c` command-line flag, which would be visible to `ps` and would
   also be one more thing to accidentally echo.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths

LAUNCHER_FILE_NAME = "launcher.json"


def launcher_config_path() -> Path:
    return paths.data_dir() / LAUNCHER_FILE_NAME


@dataclass
class LauncherConfig:
    # Repo the checkout comes from — may be an internal git host, not
    # necessarily GitHub.
    repo_url: str = ""
    # Where the checkout lives. The launcher `git pull`s this on every
    # start, so `shared_tools/` in it doubles as the auto-updating skill
    # set (the admin UI pushes into the same repo).
    cache_dir: str = ""
    # Interpreter inside the venv bootstrap created. Empty means "no venv
    # was made" — the launcher then falls back to `uvx`.
    python: str = ""
    # Corporate package index replacing PyPI, as answered in bootstrap.
    index_url: str = ""
    # Hosts pip should not verify TLS for (`pip --trusted-host`). Kept as a
    # list because users often need several (index + files host).
    trusted_hosts: list[str] = field(default_factory=list)
    # Path to a CA bundle, the *correct* fix for a MITM proxy that re-signs
    # packages: `--trusted-host` disables verification instead of trusting
    # anything. Applied through PIP_CERT/SSL_CERT_FILE/REQUESTS_CA_BUNDLE.
    ca_bundle: str = ""
    username: str = ""
    token: str = ""


def load_launcher_config() -> LauncherConfig | None:
    path = launcher_config_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return LauncherConfig(
        repo_url=data.get("repo_url") or "",
        cache_dir=data.get("cache_dir") or "",
        python=data.get("python") or "",
        index_url=data.get("index_url") or "",
        trusted_hosts=list(data.get("trusted_hosts") or []),
        ca_bundle=data.get("ca_bundle") or "",
        username=data.get("username") or "",
        token=data.get("token") or "",
    )


def save_launcher_config(config: LauncherConfig) -> Path:
    path = launcher_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")
    # Holds a plaintext repo token — same treatment config.json already gets.
    path.chmod(0o600)
    return path

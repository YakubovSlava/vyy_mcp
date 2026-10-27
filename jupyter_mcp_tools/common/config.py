"""Shared configuration for admin UI and MCP server.

Both processes read the same config file so that the admin UI can point the
MCP server at a shared tools directory without editing environment variables
on both sides.

Defaults are resolved lazily (at call time, not import time) so that
overriding ``HOME``/``MCP_TOOLS_CONFIG_PATH`` in a process (tests, in
particular) always takes effect.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from . import paths

# Shown to every connecting agent as the MCP server's own `instructions`
# (the initialize-time field most MCP clients surface verbatim as system
# context) — this is the one place a hint reaches *every* agent in every
# session automatically, with no user action needed. Used whenever
# `AppConfig.instructions` is unset; editable from the "⚙" dialog on the
# Skills page, which overrides this default.
DEFAULT_INSTRUCTIONS = (
    "Tools in this server are installed by an administrator via the "
    "jupyter-mcp-tools admin UI and are shared by every user of this "
    "JupyterLab server. If a direct shell/script execution attempt is "
    "blocked by your sandbox, do not try to work around it — only tools "
    "exposed by this MCP server run with the server's own permissions. "
    "Ask an admin to install the script as a skill (or use the "
    "create_skill tool, if installed) and call it as a tool instead."
)


def _default_shared_dir() -> Path:
    return Path.home() / ".jupyter_mcp_tools" / "shared_tools"


def config_path() -> Path:
    override = os.environ.get("MCP_TOOLS_CONFIG_PATH")
    if override:
        return Path(override)
    return paths.data_dir() / "config.json"


@dataclass
class AppConfig:
    shared_dir: str | None = None
    # Base URL of the org's Bitbucket Server/Data Center instance, baked
    # into the distributed connection notebook (see
    # admin_ui/notebook_builder.py) as the BITBUCKET_BASE_URL constant —
    # which project/repo/file within it holds the current MCP host
    # address is a notebook-level constant the admin hand-edits, not
    # stored here.
    bitbucket_base_url: str | None = None
    # HTTPS credentials for `git push`-ing the shared tools directory
    # after every skill install (see common/git_push.py) — only the main
    # admin can set these (the "⚙" dialog's edit form never renders on a
    # child relay). The shared directory is assumed to already be a git
    # repo with its remote configured; this project never touches the
    # remote itself, only pushes to it. Push is skipped entirely if
    # either of these is empty.
    git_push_username: str | None = None
    git_push_password: str | None = None
    # Overrides DEFAULT_INSTRUCTIONS above when set — see "⚙" on the
    # Skills page. Unlike the other fields here, this one is read live by
    # the MCP server's periodic sync loop (not just at process startup),
    # so an edit takes effect for the next client that connects, no
    # restart needed — the same freshness every other admin-UI edit
    # already gets for the tool list itself.
    instructions: str | None = None

    @property
    def shared_dir_path(self) -> Path:
        return Path(self.shared_dir).expanduser() if self.shared_dir else _default_shared_dir()

    @property
    def effective_instructions(self) -> str:
        return self.instructions or DEFAULT_INSTRUCTIONS


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        return AppConfig()
    data = json.loads(path.read_text())
    return AppConfig(
        shared_dir=data.get("shared_dir"),
        bitbucket_base_url=data.get("bitbucket_base_url"),
        git_push_username=data.get("git_push_username"),
        git_push_password=data.get("git_push_password"),
        instructions=data.get("instructions"),
    )


def save_config(config: AppConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2))
    # config.json now potentially holds a plaintext git password — lock
    # it down the same way admins.json already is, rather than leaving it
    # at the default (world-readable on a shared filesystem) permissions.
    path.chmod(0o600)

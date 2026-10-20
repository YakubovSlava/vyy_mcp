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

    @property
    def shared_dir_path(self) -> Path:
        return Path(self.shared_dir).expanduser() if self.shared_dir else _default_shared_dir()


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        return AppConfig()
    data = json.loads(path.read_text())
    return AppConfig(
        shared_dir=data.get("shared_dir"),
        bitbucket_base_url=data.get("bitbucket_base_url"),
    )


def save_config(config: AppConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2))

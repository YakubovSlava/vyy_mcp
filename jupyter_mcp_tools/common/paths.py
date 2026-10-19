"""Shared base directory for this project's data files.

Every data file (admins.json, config.json, users.json, session_secret)
defaults to living under this one directory, so moving them all — e.g.
to keep them outside a project checkout entirely — is a single env var
rather than four. Each file's own specific override
(MCP_TOOLS_ADMINS_PATH, etc.) still takes precedence if set, same as
before; this only changes what they fall back to.
"""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("MCP_TOOLS_DATA_DIR")
    if override:
        return Path(override)
    return Path.home() / ".jupyter_mcp_tools"

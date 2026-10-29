#!/usr/bin/env python3
"""Run the interactive setup without installing the project first.

This is the file a user runs right after cloning the repo:

    git clone https://git.example.org/team/vyy_mcp.git ~/vyy_mcp
    python3 ~/vyy_mcp/scripts/bootstrap.py

Deliberately importable from a bare interpreter: the setup it performs
creates the venv, so the venv cannot be what runs it. Everything it reaches
(`jupyter_mcp_tools.bootstrap` and what it imports) is standard-library
only — see that module's docstring.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jupyter_mcp_tools.bootstrap import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run())

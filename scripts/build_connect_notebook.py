"""Regenerates notebooks/connect_to_mcp.ipynb — the single file the main
admin distributes to every end user.

Usage:
    python scripts/build_connect_notebook.py <main-host-url> <bitbucket-script-path>

Example:
    python scripts/build_connect_notebook.py https://main.example.org:8766 /opt/scripts/resolve_host.py

Re-run this (or hand-edit the MAIN_HOST / BITBUCKET_SCRIPT_PATH constants
directly in the generated notebook's first code cell) whenever either
value changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jupyter_mcp_tools.admin_ui.notebook_builder import build_notebook_json  # noqa: E402

OUTPUT_PATH = Path(__file__).parent.parent / "notebooks" / "connect_to_mcp.ipynb"


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)

    main_host_base_url, bitbucket_script_path = sys.argv[1], sys.argv[2]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        build_notebook_json(
            bitbucket_script_path=bitbucket_script_path,
            main_host_base_url=main_host_base_url,
        )
    )
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

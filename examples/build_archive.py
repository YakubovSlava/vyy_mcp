"""Zip a tool directory (a self-describing .py file, or a SKILL.md bundle)
into an archive ready to upload through the admin UI.

Usage: python examples/build_archive.py echo_tool
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).parent


def build(tool_dir_name: str) -> Path:
    src = HERE / tool_dir_name
    if not src.is_dir():
        raise SystemExit(f"no such tool directory: {src}")

    dest = HERE / f"{tool_dir_name}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(src.rglob("*")):
            if file.is_file():
                zf.write(file, arcname=file.relative_to(src))

    print(f"wrote {dest}")
    return dest


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    build(sys.argv[1])

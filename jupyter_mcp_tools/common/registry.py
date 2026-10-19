"""Install, list and remove shared tools on disk.

Shared layout under ``shared_dir``::

    tools/
      <name>/
        manifest.json
        <module>.py
        ... extra files from the archive
"""
from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path

from .manifest import Manifest, ToolValidationError, validate_archive


class ToolAlreadyExistsError(ValueError):
    pass


class ToolNotFoundError(ValueError):
    pass


def tools_dir(shared_dir: Path) -> Path:
    return shared_dir / "tools"


def install_tool(archive_path: Path, shared_dir: Path, overwrite: bool = False) -> Manifest:
    """Validate and install a tool archive into the shared directory.

    Returns the installed tool's manifest. Raises ToolValidationError if the
    archive is malformed, or ToolAlreadyExistsError if a tool with the same
    name already exists and overwrite=False.
    """
    manifest = validate_archive(archive_path)

    dest = tools_dir(shared_dir) / manifest.name
    if dest.exists() and not overwrite:
        raise ToolAlreadyExistsError(
            f"tool '{manifest.name}' is already installed; set overwrite=True to replace it"
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(tmp_path)

        tools_dir(shared_dir).mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(tmp_path), str(dest))

    return manifest


def list_tools(shared_dir: Path) -> list[Manifest]:
    base = tools_dir(shared_dir)
    if not base.exists():
        return []

    manifests = []
    for entry in sorted(base.iterdir()):
        manifest_file = entry / "manifest.json"
        if not entry.is_dir() or not manifest_file.exists():
            continue
        try:
            manifests.append(Manifest.from_dict(json.loads(manifest_file.read_text())))
        except (ToolValidationError, json.JSONDecodeError):
            # Skip tools that became corrupted on disk rather than failing
            # the whole listing.
            continue
    return manifests


def get_tool(shared_dir: Path, name: str) -> Manifest:
    manifest_file = tools_dir(shared_dir) / name / "manifest.json"
    if not manifest_file.exists():
        raise ToolNotFoundError(f"tool '{name}' is not installed")
    return Manifest.from_dict(json.loads(manifest_file.read_text()))


def remove_tool(shared_dir: Path, name: str) -> None:
    dest = tools_dir(shared_dir) / name
    if not dest.exists():
        raise ToolNotFoundError(f"tool '{name}' is not installed")
    shutil.rmtree(dest)


def manifest_to_dict(manifest: Manifest) -> dict:
    return asdict(manifest)

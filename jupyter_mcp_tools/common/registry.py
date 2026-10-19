"""Install, list and remove shared skills on disk.

Shared layout under ``shared_dir``::

    tools/
      <name>/
        manifest.json        # code skill: see common/manifest.py
        <module>.py
        ...
      <other-name>/
        SKILL.md              # doc skill: see common/doc_skill.py
        ...

Each installed folder is one of two bundle kinds, detected by which of
``manifest.json``/``SKILL.md`` it contains — a single shared directory can
hold a mix of both.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path

from .doc_skill import DocSkillManifest, find_skill_md_name, validate_doc_skill_archive, validate_doc_skill_dir
from .manifest import Manifest, ToolValidationError, validate_archive

AnyManifest = Manifest | DocSkillManifest


class ToolAlreadyExistsError(ValueError):
    pass


class ToolNotFoundError(ValueError):
    pass


def tools_dir(shared_dir: Path) -> Path:
    return shared_dir / "tools"


def _validate_archive(archive_path: Path) -> AnyManifest:
    """Detect whether `archive_path` is a code skill (manifest.json) or a
    doc skill (SKILL.md) and validate it accordingly."""
    if not zipfile.is_zipfile(archive_path):
        raise ToolValidationError("file is not a valid zip archive")

    with zipfile.ZipFile(archive_path) as zf:
        names = zf.namelist()

    if "manifest.json" in names:
        return validate_archive(archive_path)
    if find_skill_md_name(names) is not None:
        return validate_doc_skill_archive(archive_path)
    raise ToolValidationError("archive must contain either manifest.json or SKILL.md at its root")


def install_tool(archive_path: Path, shared_dir: Path, overwrite: bool = False) -> AnyManifest:
    """Validate and install a skill archive into the shared directory.

    Returns the installed skill's manifest. Raises ToolValidationError if
    the archive is malformed, or ToolAlreadyExistsError if a skill with the
    same name already exists and overwrite=False.
    """
    manifest = _validate_archive(archive_path)

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


def _load_installed(entry: Path) -> AnyManifest | None:
    """Parse an installed skill folder's manifest, whichever kind it is.
    Returns None if it doesn't look like a valid skill of either kind."""
    if (entry / "manifest.json").exists():
        try:
            return Manifest.from_dict(json.loads((entry / "manifest.json").read_text()))
        except (ToolValidationError, json.JSONDecodeError):
            return None
    try:
        return validate_doc_skill_dir(entry)
    except ToolValidationError:
        return None


def list_tools(shared_dir: Path) -> list[AnyManifest]:
    base = tools_dir(shared_dir)
    if not base.exists():
        return []

    manifests = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        manifest = _load_installed(entry)
        if manifest is not None:
            manifests.append(manifest)
    return manifests


def remove_tool(shared_dir: Path, name: str) -> None:
    dest = tools_dir(shared_dir) / name
    if not dest.exists():
        raise ToolNotFoundError(f"tool '{name}' is not installed")
    shutil.rmtree(dest)


def manifest_to_dict(manifest: AnyManifest) -> dict:
    # `kind` is a @property, not a dataclass field, so asdict() alone
    # would drop it — the admin UI needs it to label code vs doc skills.
    return {**asdict(manifest), "kind": manifest.kind}

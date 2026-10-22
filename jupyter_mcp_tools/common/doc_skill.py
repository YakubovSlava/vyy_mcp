"""Doc-skill bundles: folders (or zip archives) containing a ``SKILL.md``
(inspired by Claude's "Agent Skills" format, simplified) instead of a
Python entrypoint.

Layout::

    <skill-name>/
      SKILL.md             # plain markdown body -- no special syntax required
      .skill_meta.json      # {"description": "..."} -- written at install time
      ... any other files/folders (scripts, references, assets, ...)

Per explicit request, ``SKILL.md`` itself carries no YAML frontmatter and
no required syntax at all — it's just the markdown instructions a human
would naturally write. ``name`` and ``description`` are supplied by the
admin directly in the upload form instead (the doc-skill equivalent of a
code skill's function name/docstring): ``name`` becomes both the
installed folder name and the MCP tool name, and ``description`` is
persisted to a small ``.skill_meta.json`` sidecar file so it survives
being re-read from disk on every scan (see ``registry.list_tools``) —
there's nowhere else left in the bundle to keep it once the frontmatter
is gone.

The MCP tool built from this bundle (see mcp_server/server.py) uses
`description` as the *tool's own* MCP description, so an agent can decide
when to call it purely from `tools/list` output — the same "progressive
disclosure" idea the Agent Skills format itself is built around. Calling
it with no arguments returns the markdown body plus a list of the
bundle's other files; calling it with `file="<relative/path>"` returns
that specific file's content, so large bundles don't have to be returned
in full on every call.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .manifest import NAME_RE, ToolValidationError

_SKILL_MD_NAMES = ("SKILL.md", "skill.md")
_META_FILENAME = ".skill_meta.json"


@dataclass
class DocSkillManifest:
    name: str
    description: str
    body: str
    version: str = "1.0.0"
    files: tuple[str, ...] = ()  # relative paths of auxiliary files (excludes SKILL.md/meta)

    @property
    def kind(self) -> str:
        return "doc"


def find_skill_md_name(names: list[str]) -> str | None:
    """Return the SKILL.md member name among `names` (root-level only), if any."""
    for candidate in _SKILL_MD_NAMES:
        if candidate in names:
            return candidate
    return None


def check_name_and_description(name: str, description: str) -> None:
    if not name:
        raise ToolValidationError("doc skill needs a name — enter it in the upload form")
    if not NAME_RE.match(name):
        raise ToolValidationError("name must match [a-z0-9][a-z0-9_-]{1,63}")
    if not description:
        raise ToolValidationError("doc skill needs a description — enter it in the upload form")


def validate_doc_skill_archive(archive_path: Path, *, name: str, description: str) -> DocSkillManifest:
    """Validate a doc-skill `.zip` and return its manifest.

    `name`/`description` come from the admin's upload form, not from the
    file itself — SKILL.md is plain markdown with no required syntax.
    Raises ToolValidationError on any structural problem.
    """
    check_name_and_description(name, description)

    if not zipfile.is_zipfile(archive_path):
        raise ToolValidationError("file is not a valid zip archive")

    with zipfile.ZipFile(archive_path) as zf:
        names = zf.namelist()
        for member in names:
            if member.startswith("/") or ".." in Path(member).parts:
                raise ToolValidationError(f"archive contains unsafe path: {member}")

        skill_md_name = find_skill_md_name(names)
        if skill_md_name is None:
            raise ToolValidationError("archive is missing SKILL.md (or skill.md) at its root")

        body = zf.read(skill_md_name).decode("utf-8", errors="replace").strip()
        if not body:
            raise ToolValidationError("SKILL.md has no content")

        files = tuple(sorted(n for n in names if n != skill_md_name and not n.endswith("/")))

    return DocSkillManifest(name=name, description=description, body=body, files=files)


def validate_doc_skill_dir(skill_dir: Path) -> DocSkillManifest:
    """Validate a doc-skill that's already a directory on disk (used when
    re-reading an installed skill, not an upload). `name` is the folder
    name; `description` is read back from the `.skill_meta.json` sidecar
    written at install time."""
    skill_md_path = None
    for candidate in _SKILL_MD_NAMES:
        if (skill_dir / candidate).exists():
            skill_md_path = skill_dir / candidate
            break
    if skill_md_path is None:
        raise ToolValidationError("directory is missing SKILL.md (or skill.md)")

    meta_path = skill_dir / _META_FILENAME
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        description = str(meta["description"])
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise ToolValidationError(f"missing or invalid {_META_FILENAME}") from exc

    body = skill_md_path.read_text(encoding="utf-8", errors="replace").strip()
    if not body:
        raise ToolValidationError("SKILL.md has no content")

    files = tuple(
        sorted(
            str(p.relative_to(skill_dir))
            for p in skill_dir.rglob("*")
            if p.is_file() and p not in (skill_md_path, meta_path)
        )
    )
    return DocSkillManifest(name=skill_dir.name, description=description, body=body, files=files)


def write_meta(skill_dir: Path, description: str) -> None:
    """Persist the admin-supplied description alongside an installed doc
    skill, so `validate_doc_skill_dir` can recover it on every re-scan."""
    (skill_dir / _META_FILENAME).write_text(json.dumps({"description": description}), encoding="utf-8")


def create_doc_skill_dir(skill_dir: Path, *, description: str, body: str) -> DocSkillManifest:
    """Write a brand-new doc skill straight to disk from admin-typed
    name/description/body — no archive involved at all. `name` is
    `skill_dir.name` (the caller picks the destination folder); the
    caller is also responsible for validating it and checking
    `check_name_and_description` before calling this, same as
    `validate_doc_skill_archive` does for an upload."""
    body = body.strip()
    if not body:
        raise ToolValidationError("SKILL.md has no content")

    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    write_meta(skill_dir, description)
    return DocSkillManifest(name=skill_dir.name, description=description, body=body, files=())

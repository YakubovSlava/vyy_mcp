"""Doc-skill bundles: folders (or zip archives) containing a ``SKILL.md``
(a.k.a. the Claude "Agent Skills" format) instead of a ``manifest.json`` +
Python entrypoint.

Layout::

    <skill-name>/
      SKILL.md          # YAML frontmatter (name, description) + markdown body
      ... any other files/folders (scripts, references, assets, ...)

``SKILL.md``::

    ---
    name: my-skill
    description: Use this when the user asks about X; explains how to do Y.
    ---

    # My Skill

    <markdown body — instructions for whoever/whatever is using this skill>

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

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from .manifest import NAME_RE, ToolValidationError

_SKILL_MD_NAMES = ("SKILL.md", "skill.md")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


@dataclass
class DocSkillManifest:
    name: str
    description: str
    body: str
    version: str = "1.0.0"
    files: tuple[str, ...] = ()  # relative paths of auxiliary files (excludes SKILL.md itself)

    @property
    def kind(self) -> str:
        return "doc"


def find_skill_md_name(names: list[str]) -> str | None:
    """Return the SKILL.md member name among `names` (root-level only), if any."""
    for candidate in _SKILL_MD_NAMES:
        if candidate in names:
            return candidate
    return None


def parse_skill_md(raw: str) -> tuple[dict, str]:
    """Split SKILL.md into (frontmatter dict, body markdown)."""
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        raise ToolValidationError("SKILL.md must start with a '---' YAML frontmatter block")

    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ToolValidationError(f"SKILL.md frontmatter is not valid YAML: {exc}") from exc

    if not isinstance(frontmatter, dict):
        raise ToolValidationError("SKILL.md frontmatter must be a YAML mapping")

    return frontmatter, match.group(2).strip()


def _manifest_from_parts(frontmatter: dict, body: str, files: tuple[str, ...]) -> DocSkillManifest:
    missing = [f for f in ("name", "description") if not frontmatter.get(f)]
    if missing:
        raise ToolValidationError(f"SKILL.md frontmatter missing field(s): {', '.join(missing)}")

    name = str(frontmatter["name"])
    if not NAME_RE.match(name):
        raise ToolValidationError("SKILL.md 'name' must match [a-z0-9][a-z0-9_-]{1,63}")

    if not body:
        raise ToolValidationError("SKILL.md has no body content after the frontmatter block")

    return DocSkillManifest(
        name=name,
        description=str(frontmatter["description"]),
        body=body,
        version=str(frontmatter.get("version", "1.0.0")),
        files=files,
    )


def validate_doc_skill_archive(archive_path: Path) -> DocSkillManifest:
    """Validate a doc-skill `.zip` and return its manifest.

    Raises ToolValidationError on any structural problem.
    """
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

        frontmatter, body = parse_skill_md(zf.read(skill_md_name).decode("utf-8", errors="replace"))
        files = tuple(sorted(n for n in names if n != skill_md_name and not n.endswith("/")))

    return _manifest_from_parts(frontmatter, body, files)


def validate_doc_skill_dir(skill_dir: Path) -> DocSkillManifest:
    """Validate a doc-skill that's already a directory on disk (used when
    re-reading an installed skill, not an upload)."""
    skill_md_path = None
    for candidate in _SKILL_MD_NAMES:
        if (skill_dir / candidate).exists():
            skill_md_path = skill_dir / candidate
            break
    if skill_md_path is None:
        raise ToolValidationError("directory is missing SKILL.md (or skill.md)")

    frontmatter, body = parse_skill_md(skill_md_path.read_text(encoding="utf-8", errors="replace"))
    files = tuple(
        sorted(
            str(p.relative_to(skill_dir))
            for p in skill_dir.rglob("*")
            if p.is_file() and p != skill_md_path
        )
    )
    return _manifest_from_parts(frontmatter, body, files)

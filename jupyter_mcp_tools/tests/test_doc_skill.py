import zipfile

import pytest

from jupyter_mcp_tools.common.doc_skill import (
    find_skill_md_name,
    parse_skill_md,
    validate_doc_skill_archive,
    validate_doc_skill_dir,
)
from jupyter_mcp_tools.common.manifest import ToolValidationError

VALID_SKILL_MD = """---
name: repo-explainer
description: Use this when the user asks about the repo's structure.
---

# Repo explainer

Body content here.
"""


def test_parse_skill_md_splits_frontmatter_and_body():
    frontmatter, body = parse_skill_md(VALID_SKILL_MD)
    assert frontmatter == {
        "name": "repo-explainer",
        "description": "Use this when the user asks about the repo's structure.",
    }
    assert body == "# Repo explainer\n\nBody content here."


def test_parse_skill_md_requires_frontmatter_block():
    with pytest.raises(ToolValidationError, match="YAML frontmatter"):
        parse_skill_md("# No frontmatter here\n")


def test_parse_skill_md_rejects_invalid_yaml():
    with pytest.raises(ToolValidationError, match="not valid YAML"):
        parse_skill_md("---\nname: [unclosed\n---\nbody\n")


def test_find_skill_md_name_prefers_either_case():
    assert find_skill_md_name(["SKILL.md", "other.txt"]) == "SKILL.md"
    assert find_skill_md_name(["skill.md"]) == "skill.md"
    assert find_skill_md_name(["manifest.json"]) is None


def _make_zip(tmp_path, name, skill_md=VALID_SKILL_MD, extra_files=None):
    archive = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("SKILL.md", skill_md)
        for path, content in (extra_files or {}).items():
            zf.writestr(path, content)
    return archive


def test_validate_doc_skill_archive_happy_path(tmp_path):
    archive = _make_zip(tmp_path, "ok", extra_files={"references/notes.md": "notes"})
    manifest = validate_doc_skill_archive(archive)
    assert manifest.name == "repo-explainer"
    assert manifest.description.startswith("Use this when")
    assert manifest.files == ("references/notes.md",)
    assert manifest.version == "1.0.0"


def test_validate_doc_skill_archive_not_a_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    with pytest.raises(ToolValidationError, match="not a valid zip"):
        validate_doc_skill_archive(bad)


def test_validate_doc_skill_archive_missing_skill_md(tmp_path):
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(ToolValidationError, match="missing SKILL.md"):
        validate_doc_skill_archive(archive)


def test_validate_doc_skill_archive_missing_required_field(tmp_path):
    archive = _make_zip(tmp_path, "missing-desc", skill_md="---\nname: foo\n---\nbody\n")
    with pytest.raises(ToolValidationError, match="missing field"):
        validate_doc_skill_archive(archive)


def test_validate_doc_skill_archive_invalid_name(tmp_path):
    skill_md = "---\nname: Not Valid Name\ndescription: x\n---\nbody\n"
    archive = _make_zip(tmp_path, "bad-name", skill_md=skill_md)
    with pytest.raises(ToolValidationError, match="name"):
        validate_doc_skill_archive(archive)


def test_validate_doc_skill_archive_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("SKILL.md", VALID_SKILL_MD)
        zf.writestr("../../etc/passwd", "oops")
    with pytest.raises(ToolValidationError, match="unsafe path"):
        validate_doc_skill_archive(archive)


def test_validate_doc_skill_archive_empty_body(tmp_path):
    archive = _make_zip(tmp_path, "empty-body", skill_md="---\nname: foo\ndescription: x\n---\n")
    with pytest.raises(ToolValidationError, match="no body content"):
        validate_doc_skill_archive(archive)


def test_validate_doc_skill_dir(tmp_path):
    skill_dir = tmp_path / "repo-explainer"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(VALID_SKILL_MD)
    (skill_dir / "references" / "notes.md").write_text("notes")

    manifest = validate_doc_skill_dir(skill_dir)
    assert manifest.name == "repo-explainer"
    assert manifest.files == ("references/notes.md",)


def test_validate_doc_skill_dir_missing_skill_md(tmp_path):
    skill_dir = tmp_path / "empty"
    skill_dir.mkdir()
    with pytest.raises(ToolValidationError, match="missing SKILL.md"):
        validate_doc_skill_dir(skill_dir)

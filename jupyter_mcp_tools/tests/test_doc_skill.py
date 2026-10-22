import json
import zipfile

import pytest

from jupyter_mcp_tools.common.doc_skill import (
    find_skill_md_name,
    validate_doc_skill_archive,
    validate_doc_skill_dir,
    write_meta,
)
from jupyter_mcp_tools.common.manifest import ToolValidationError

BODY = "# Repo explainer\n\nBody content here.\n"


def test_find_skill_md_name_prefers_either_case():
    assert find_skill_md_name(["SKILL.md", "other.txt"]) == "SKILL.md"
    assert find_skill_md_name(["skill.md"]) == "skill.md"
    assert find_skill_md_name(["manifest.json"]) is None


def _make_zip(tmp_path, name, skill_md=BODY, extra_files=None):
    archive = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("SKILL.md", skill_md)
        for path, content in (extra_files or {}).items():
            zf.writestr(path, content)
    return archive


def test_validate_doc_skill_archive_happy_path(tmp_path):
    archive = _make_zip(tmp_path, "ok", extra_files={"references/notes.md": "notes"})
    manifest = validate_doc_skill_archive(
        archive, name="repo-explainer", description="Explains the repo.", group="docs"
    )
    assert manifest.name == "repo-explainer"
    assert manifest.description == "Explains the repo."
    assert manifest.body == BODY.strip()
    assert manifest.files == ("references/notes.md",)
    assert manifest.version == "1.0.0"
    assert manifest.group == "docs"


def test_validate_doc_skill_archive_group_defaults_to_empty(tmp_path):
    archive = _make_zip(tmp_path, "ok")
    manifest = validate_doc_skill_archive(archive, name="repo-explainer", description="Explains the repo.")
    assert manifest.group == ""


def test_validate_doc_skill_archive_requires_name(tmp_path):
    archive = _make_zip(tmp_path, "no-name")
    with pytest.raises(ToolValidationError, match="needs a name"):
        validate_doc_skill_archive(archive, name="", description="desc")


def test_validate_doc_skill_archive_requires_description(tmp_path):
    archive = _make_zip(tmp_path, "no-desc")
    with pytest.raises(ToolValidationError, match="needs a description"):
        validate_doc_skill_archive(archive, name="repo-explainer", description="")


def test_validate_doc_skill_archive_rejects_invalid_name(tmp_path):
    archive = _make_zip(tmp_path, "bad-name")
    with pytest.raises(ToolValidationError, match="must match"):
        validate_doc_skill_archive(archive, name="Not Valid Name", description="desc")


def test_validate_doc_skill_archive_not_a_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    with pytest.raises(ToolValidationError, match="not a valid zip"):
        validate_doc_skill_archive(bad, name="xx", description="y")


def test_validate_doc_skill_archive_missing_skill_md(tmp_path):
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(ToolValidationError, match="missing SKILL.md"):
        validate_doc_skill_archive(archive, name="xx", description="y")


def test_validate_doc_skill_archive_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("SKILL.md", BODY)
        zf.writestr("../../etc/passwd", "oops")
    with pytest.raises(ToolValidationError, match="unsafe path"):
        validate_doc_skill_archive(archive, name="xx", description="y")


def test_validate_doc_skill_archive_empty_body(tmp_path):
    archive = _make_zip(tmp_path, "empty-body", skill_md="   \n")
    with pytest.raises(ToolValidationError, match="no content"):
        validate_doc_skill_archive(archive, name="xx", description="y")


def test_validate_doc_skill_dir(tmp_path):
    skill_dir = tmp_path / "repo-explainer"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(BODY)
    (skill_dir / "references" / "notes.md").write_text("notes")
    write_meta(skill_dir, description="Explains the repo.", group="docs")

    manifest = validate_doc_skill_dir(skill_dir)
    assert manifest.name == "repo-explainer"
    assert manifest.description == "Explains the repo."
    assert manifest.files == ("references/notes.md",)
    assert manifest.group == "docs"


def test_validate_doc_skill_dir_missing_group_in_legacy_meta_defaults_to_empty(tmp_path):
    # .skill_meta.json files written before `group` existed only have
    # "description" -- must not break re-reading them.
    skill_dir = tmp_path / "old-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(BODY)
    (skill_dir / ".skill_meta.json").write_text('{"description": "Legacy."}')

    manifest = validate_doc_skill_dir(skill_dir)
    assert manifest.group == ""


def test_validate_doc_skill_dir_missing_skill_md(tmp_path):
    skill_dir = tmp_path / "empty"
    skill_dir.mkdir()
    with pytest.raises(ToolValidationError, match="missing SKILL.md"):
        validate_doc_skill_dir(skill_dir)


def test_validate_doc_skill_dir_missing_meta(tmp_path):
    skill_dir = tmp_path / "no-meta"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(BODY)
    with pytest.raises(ToolValidationError, match="missing or invalid"):
        validate_doc_skill_dir(skill_dir)


def test_validate_doc_skill_dir_invalid_meta_json(tmp_path):
    skill_dir = tmp_path / "bad-meta"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(BODY)
    (skill_dir / ".skill_meta.json").write_text("not json")
    with pytest.raises(ToolValidationError, match="missing or invalid"):
        validate_doc_skill_dir(skill_dir)


def test_write_meta_round_trips(tmp_path):
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    write_meta(skill_dir, description="hello", group="networking")
    assert json.loads((skill_dir / ".skill_meta.json").read_text()) == {
        "description": "hello",
        "group": "networking",
    }

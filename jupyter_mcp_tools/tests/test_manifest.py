import pytest

from jupyter_mcp_tools.common.manifest import ToolValidationError, validate_archive
from jupyter_mcp_tools.tests.conftest import VALID_MANIFEST, VALID_MODULE


def test_valid_archive_passes(make_archive):
    archive = make_archive("greet", VALID_MANIFEST, VALID_MODULE)
    manifest = validate_archive(archive)
    assert manifest.name == "greet"
    assert manifest.entrypoint == "tool:greet"


def test_not_a_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    with pytest.raises(ToolValidationError, match="not a valid zip"):
        validate_archive(bad)


def test_missing_manifest_json(tmp_path):
    import zipfile

    archive = tmp_path / "x.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("tool.py", VALID_MODULE)
    with pytest.raises(ToolValidationError, match="manifest.json"):
        validate_archive(archive)


def test_invalid_name(make_archive):
    manifest = VALID_MANIFEST.replace('"greet"', '"Invalid Name!"')
    archive = make_archive("bad", manifest, VALID_MODULE)
    with pytest.raises(ToolValidationError, match="name"):
        validate_archive(archive)


def test_missing_entrypoint_module(make_archive):
    manifest = """{
      "name": "greet",
      "version": "1.0.0",
      "description": "desc",
      "entrypoint": "missing_module:greet"
    }"""
    archive = make_archive("greet", manifest, VALID_MODULE)
    with pytest.raises(ToolValidationError, match="not found in archive"):
        validate_archive(archive)


def test_missing_entrypoint_function(make_archive):
    manifest = VALID_MANIFEST.replace("tool:greet", "tool:does_not_exist")
    archive = make_archive("greet", manifest, VALID_MODULE)
    with pytest.raises(ToolValidationError, match="not defined"):
        validate_archive(archive)


def test_syntax_error_in_module(make_archive):
    archive = make_archive("greet", VALID_MANIFEST, "def greet(name:\n")
    with pytest.raises(ToolValidationError, match="syntax error"):
        validate_archive(archive)


def test_path_traversal_rejected(tmp_path):
    import zipfile

    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", VALID_MANIFEST)
        zf.writestr("../../etc/passwd", "oops")
    with pytest.raises(ToolValidationError, match="unsafe path"):
        validate_archive(archive)

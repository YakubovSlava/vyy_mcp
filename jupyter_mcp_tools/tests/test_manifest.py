import zipfile

import pytest

from jupyter_mcp_tools.common.manifest import ToolValidationError, validate_archive
from jupyter_mcp_tools.tests.conftest import VALID_MODULE


def test_valid_archive_passes(make_archive):
    archive = make_archive("greet", VALID_MODULE)
    manifest = validate_archive(archive)
    assert manifest.name == "greet"
    assert manifest.entrypoint == "tool:greet"
    assert manifest.description == "Greets someone by name."
    assert manifest.version == "1.0.0"


def test_not_a_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    with pytest.raises(ToolValidationError, match="not a valid zip"):
        validate_archive(bad)


def test_no_python_file_at_root(tmp_path):
    archive = tmp_path / "x.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(ToolValidationError, match="no .py file"):
        validate_archive(archive)


def test_more_than_one_python_file_at_root_rejected(make_archive, tmp_path):
    archive = tmp_path / "two.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("tool.py", VALID_MODULE)
        zf.writestr("helper.py", VALID_MODULE)
    with pytest.raises(ToolValidationError, match="exactly one .py file"):
        validate_archive(archive)


def test_invalid_function_name_rejected(make_archive):
    module = 'def Not_Valid(name: str) -> str:\n    """Docstring."""\n    return name\n'
    archive = make_archive("bad", module)
    with pytest.raises(ToolValidationError, match="function name"):
        validate_archive(archive)


def test_more_than_one_top_level_function_rejected(make_archive):
    module = VALID_MODULE + '\n\ndef other(name: str) -> str:\n    """Other docstring."""\n    return name\n'
    archive = make_archive("greet", module)
    with pytest.raises(ToolValidationError, match="exactly one top-level function"):
        validate_archive(archive)


def test_no_top_level_function_rejected(make_archive):
    archive = make_archive("empty", "x = 1\n")
    with pytest.raises(ToolValidationError, match="no top-level function"):
        validate_archive(archive)


def test_missing_docstring_rejected(make_archive):
    module = "def greet(name: str) -> str:\n    return f\"Hello, {name}!\"\n"
    archive = make_archive("greet", module)
    with pytest.raises(ToolValidationError, match="docstring"):
        validate_archive(archive)


def test_syntax_error_in_module(make_archive):
    archive = make_archive("greet", "def greet(name:\n")
    with pytest.raises(ToolValidationError, match="syntax error"):
        validate_archive(archive)


def test_custom_version_picked_up(make_archive):
    module = '__version__ = "2.3.1"\n\n' + VALID_MODULE
    archive = make_archive("greet", module)
    manifest = validate_archive(archive)
    assert manifest.version == "2.3.1"


def test_path_traversal_rejected(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("tool.py", VALID_MODULE)
        zf.writestr("../../etc/passwd", "oops")
    with pytest.raises(ToolValidationError, match="unsafe path"):
        validate_archive(archive)

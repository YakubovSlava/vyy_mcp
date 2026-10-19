import zipfile
from pathlib import Path

import pytest


def _write_archive(tmp_path: Path, name: str, manifest_json: str, module_source: str, module_name: str = "tool") -> Path:
    archive_path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("manifest.json", manifest_json)
        zf.writestr(f"{module_name}.py", module_source)
    return archive_path


@pytest.fixture
def make_archive(tmp_path):
    return lambda *args, **kwargs: _write_archive(tmp_path, *args, **kwargs)


@pytest.fixture
def shared_dir(tmp_path):
    d = tmp_path / "shared"
    d.mkdir()
    return d


VALID_MANIFEST = """{
  "name": "greet",
  "version": "1.0.0",
  "description": "Greets someone",
  "entrypoint": "tool:greet"
}"""

VALID_MODULE = """
def greet(name: str) -> str:
    return f"Hello, {name}!"
"""

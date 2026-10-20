import zipfile
from pathlib import Path

import pytest


def _write_archive(tmp_path: Path, name: str, module_source: str, module_name: str = "tool") -> Path:
    archive_path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
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


VALID_MODULE = """
def greet(name: str) -> str:
    \"\"\"Greets someone by name.\"\"\"
    return f"Hello, {name}!"
"""

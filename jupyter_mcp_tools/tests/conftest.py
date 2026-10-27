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
def shared_dir(tmp_path, monkeypatch):
    # mcp_server.server now calls common.config.load_config() (for the
    # live-editable `instructions` field) — isolate HOME/
    # MCP_TOOLS_CONFIG_PATH the same way test_admin_api.py's app_module
    # fixture does, so tests never read or write the real developer's
    # ~/.jupyter_mcp_tools/config.json.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MCP_TOOLS_CONFIG_PATH", str(tmp_path / "config.json"))
    d = tmp_path / "shared"
    d.mkdir()
    return d


VALID_MODULE = """
def greet(name: str) -> str:
    \"\"\"Greets someone by name.\"\"\"
    return f"Hello, {name}!"
"""

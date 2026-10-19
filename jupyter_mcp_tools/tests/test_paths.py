from pathlib import Path

from jupyter_mcp_tools.common import paths


def test_data_dir_defaults_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_TOOLS_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.data_dir() == tmp_path / ".jupyter_mcp_tools"


def test_data_dir_honors_override(monkeypatch, tmp_path):
    override = tmp_path / "elsewhere"
    monkeypatch.setenv("MCP_TOOLS_DATA_DIR", str(override))
    assert paths.data_dir() == override


def test_admins_config_users_session_share_data_dir_by_default(monkeypatch, tmp_path):
    from jupyter_mcp_tools.common import auth, config, session, users

    monkeypatch.delenv("MCP_TOOLS_ADMINS_PATH", raising=False)
    monkeypatch.delenv("MCP_TOOLS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("MCP_TOOLS_USERS_PATH", raising=False)
    monkeypatch.delenv("MCP_TOOLS_SESSION_SECRET_PATH", raising=False)
    monkeypatch.setenv("MCP_TOOLS_DATA_DIR", str(tmp_path))

    assert auth.admins_path() == tmp_path / "admins.json"
    assert config.config_path() == tmp_path / "config.json"
    assert users.users_path() == tmp_path / "users.json"
    assert session._secret_key_path() == tmp_path / "session_secret"


def test_specific_override_still_wins_over_data_dir(monkeypatch, tmp_path):
    from jupyter_mcp_tools.common import auth

    monkeypatch.setenv("MCP_TOOLS_DATA_DIR", str(tmp_path / "shared"))
    monkeypatch.setenv("MCP_TOOLS_ADMINS_PATH", str(tmp_path / "custom_admins.json"))
    assert auth.admins_path() == tmp_path / "custom_admins.json"

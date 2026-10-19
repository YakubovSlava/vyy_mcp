from jupyter_mcp_tools.common import auth


def test_add_and_verify(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOOLS_ADMINS_PATH", str(tmp_path / "admins.json"))

    assert auth.list_admins() == []
    assert not auth.has_any_admin()

    auth.add_admin("alice", "s3cret")

    assert auth.has_any_admin()
    assert auth.list_admins() == ["alice"]
    assert auth.verify_admin("alice", "s3cret")
    assert not auth.verify_admin("alice", "wrong")
    assert not auth.verify_admin("bob", "s3cret")


def test_remove_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOOLS_ADMINS_PATH", str(tmp_path / "admins.json"))

    auth.add_admin("alice", "s3cret")
    assert auth.remove_admin("alice") is True
    assert auth.list_admins() == []
    assert auth.remove_admin("alice") is False


def test_multiple_admins_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOOLS_ADMINS_PATH", str(tmp_path / "admins.json"))

    auth.add_admin("alice", "alice-pw")
    auth.add_admin("bob", "bob-pw")

    assert auth.list_admins() == ["alice", "bob"]
    assert auth.verify_admin("alice", "alice-pw")
    assert auth.verify_admin("bob", "bob-pw")
    assert not auth.verify_admin("alice", "bob-pw")


def test_password_not_stored_in_plaintext(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOOLS_ADMINS_PATH", str(tmp_path / "admins.json"))

    auth.add_admin("alice", "super-secret-password")
    raw = (tmp_path / "admins.json").read_text()
    assert "super-secret-password" not in raw


def test_resetting_password_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOOLS_ADMINS_PATH", str(tmp_path / "admins.json"))

    auth.add_admin("alice", "old-pw")
    auth.add_admin("alice", "new-pw")

    assert not auth.verify_admin("alice", "old-pw")
    assert auth.verify_admin("alice", "new-pw")
    assert auth.list_admins() == ["alice"]

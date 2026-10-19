from jupyter_mcp_tools.common import users


def test_record_user_creates_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOOLS_USERS_PATH", str(tmp_path / "users.json"))

    users.record_user("alice")

    entries = users.list_users()
    assert len(entries) == 1
    assert entries[0]["username"] == "alice"
    assert entries[0]["first_seen"] == entries[0]["last_seen"]


def test_record_user_twice_updates_last_seen_only(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOOLS_USERS_PATH", str(tmp_path / "users.json"))

    users.record_user("alice")
    first_seen = users.list_users()[0]["first_seen"]

    users.record_user("alice")
    entries = users.list_users()

    assert len(entries) == 1
    assert entries[0]["first_seen"] == first_seen
    assert entries[0]["last_seen"] >= first_seen


def test_multiple_users_tracked_independently(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOOLS_USERS_PATH", str(tmp_path / "users.json"))

    users.record_user("alice")
    users.record_user("bob")

    usernames = {u["username"] for u in users.list_users()}
    assert usernames == {"alice", "bob"}


def test_list_users_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOOLS_USERS_PATH", str(tmp_path / "users.json"))
    assert users.list_users() == []


def test_list_users_skips_legacy_schema_entries(tmp_path, monkeypatch):
    # Regression test: an older version of this store keyed entries by a
    # random invite code and allowed username/last_seen to be None (e.g.
    # an invite nobody has redeemed yet). list_users() used to sort on
    # last_seen unconditionally and crash comparing None to str the first
    # time one of these leftover entries was mixed in with real ones.
    path = tmp_path / "users.json"
    monkeypatch.setenv("MCP_TOOLS_USERS_PATH", str(path))
    path.write_text(
        '{"JGqHHLNPtEA": {"code": "JGqHHLNPtEA", "label": null, '
        '"username": null, "created_at": "2026-10-19T10:05:55+00:00", '
        '"last_seen": null}}'
    )

    users.record_user("alice")

    entries = users.list_users()
    assert [u["username"] for u in entries] == ["alice"]

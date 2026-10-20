import importlib

import pytest
from fastapi.testclient import TestClient

from jupyter_mcp_tools.common import auth

ADMIN_USER = "alice"
ADMIN_PASSWORD = "s3cret"


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MCP_TOOLS_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("MCP_TOOLS_ADMINS_PATH", str(tmp_path / "admins.json"))
    monkeypatch.setenv("MCP_TOOLS_USERS_PATH", str(tmp_path / "users.json"))
    monkeypatch.setenv("MCP_TOOLS_SESSION_SECRET_PATH", str(tmp_path / "session_secret"))
    monkeypatch.delenv("MCP_TOOLS_MAIN_HOST", raising=False)

    auth.add_admin(ADMIN_USER, ADMIN_PASSWORD)

    from jupyter_mcp_tools.admin_ui import main as admin_main

    importlib.reload(admin_main)
    return admin_main


@pytest.fixture
def client(app_module):
    test_client = TestClient(app_module.app)
    test_client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    return test_client


def test_register_requires_no_admin_auth(app_module):
    # /api/users/register is intentionally public — end users have no
    # admin account (see common/users.py, PLAN.md).
    anon = TestClient(app_module.app)
    r = anon.post("/api/users/register", data={"username": "dave.jones"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_registered_user_shows_up_in_admin_ui(client):
    client.post("/api/users/register", data={"username": "dave.jones"})

    r = client.get("/users")
    assert "dave.jones" in r.text
    assert "Пока ни один пользователь не подключился" not in r.text


def test_no_users_yet_shows_empty_state(client):
    r = client.get("/users")
    assert "Пока ни один пользователь не подключился" in r.text


def test_users_page_requires_admin_login(app_module):
    anon = TestClient(app_module.app)
    r = anon.get("/users", follow_redirects=False)
    assert r.headers["location"] == "login"


def test_skills_and_users_pages_link_to_each_other(client):
    r = client.get("/")
    assert 'href="users"' in r.text

    r = client.get("/users")
    assert 'href="."' in r.text

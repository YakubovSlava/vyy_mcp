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


def test_docs_page_requires_admin_login(app_module):
    anon = TestClient(app_module.app)
    r = anon.get("/docs", follow_redirects=False)
    assert r.headers["location"] == "login"


def test_docs_page_shows_manifest_example(client):
    r = client.get("/docs")
    assert r.status_code == 200
    assert "manifest.json" in r.text
    assert '"entrypoint": "tool:echo"' in r.text


def test_docs_page_shows_multi_file_result_convention(client):
    r = client.get("/docs")
    assert "skill_results" in r.text
    assert "files_result" in r.text


def test_docs_page_shows_configured_shared_dir(client, tmp_path):
    from jupyter_mcp_tools.common import config as cfg_module

    cfg_module.save_config(cfg_module.AppConfig(shared_dir=str(tmp_path / "custom_shared")))

    r = client.get("/docs")
    assert str(tmp_path / "custom_shared") in r.text


def test_nav_links_to_docs(client):
    r = client.get("/")
    assert 'href="docs"' in r.text

    r = client.get("/docs")
    assert 'href="."' in r.text
    assert 'href="users"' in r.text

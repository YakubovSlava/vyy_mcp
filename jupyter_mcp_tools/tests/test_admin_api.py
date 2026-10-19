import importlib
import zipfile

import pytest
from fastapi.testclient import TestClient

from jupyter_mcp_tools.common import auth
from jupyter_mcp_tools.tests.conftest import VALID_MANIFEST, VALID_MODULE

ADMIN_USER = "alice"
ADMIN_PASSWORD = "s3cret"


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    # Isolate both the config file location and the default shared_dir
    # fallback (which is derived from the home directory) so tests never
    # touch the real user's ~/.jupyter_mcp_tools.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MCP_TOOLS_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("MCP_TOOLS_ADMINS_PATH", str(tmp_path / "admins.json"))
    monkeypatch.setenv("MCP_TOOLS_SESSION_SECRET_PATH", str(tmp_path / "session_secret"))
    monkeypatch.delenv("MCP_TOOLS_MAIN_HOST", raising=False)
    auth.add_admin(ADMIN_USER, ADMIN_PASSWORD)

    # config module caches nothing at import time, but admin_ui.main is
    # already imported by the time this fixture runs in the test session;
    # reloading common.config is not needed since config_path() reads the
    # env var on every call.
    from jupyter_mcp_tools.admin_ui import main as admin_main

    importlib.reload(admin_main)
    return admin_main


@pytest.fixture
def anon_client(app_module):
    """A client with no session cookie and no Basic Auth set."""
    return TestClient(app_module.app)


@pytest.fixture
def client(app_module):
    """A client already signed in through the HTML login form."""
    test_client = TestClient(app_module.app)
    r = test_client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert r.status_code == 200  # followed the post-login redirect to "/"
    return test_client


def _archive_bytes(tmp_path, manifest=VALID_MANIFEST, module=VALID_MODULE):
    path = tmp_path / "upload.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", manifest)
        zf.writestr("tool.py", module)
    return path.read_bytes()


def test_index_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "No skills installed yet" in r.text


def test_upload_install_list_and_delete(client, tmp_path):
    data = _archive_bytes(tmp_path)
    r = client.post(
        "/tools/upload",
        files={"archive": ("greet.zip", data, "application/zip")},
    )
    assert r.status_code == 200
    assert "Installed &#39;greet&#39;" in r.text or "Installed 'greet'" in r.text

    r = client.get("/api/tools", auth=(ADMIN_USER, ADMIN_PASSWORD))
    assert r.status_code == 200
    names = [t["name"] for t in r.json()]
    assert names == ["greet"]

    r = client.post("/tools/greet/delete")
    assert r.status_code == 200

    r = client.get("/api/tools", auth=(ADMIN_USER, ADMIN_PASSWORD))
    assert r.json() == []


def test_upload_duplicate_without_overwrite_shows_error(client, tmp_path):
    data = _archive_bytes(tmp_path)
    client.post("/tools/upload", files={"archive": ("greet.zip", data, "application/zip")})
    r = client.post("/tools/upload", files={"archive": ("greet.zip", data, "application/zip")})
    assert "already installed" in r.text


def test_upload_invalid_archive_shows_error(client):
    r = client.post(
        "/tools/upload",
        files={"archive": ("bad.zip", b"not a zip", "application/zip")},
    )
    assert "Error:" in r.text


def test_set_shared_dir(client, tmp_path):
    new_dir = tmp_path / "other_shared"
    r = client.post("/config/shared-dir", data={"path": str(new_dir)})
    assert r.status_code == 200
    assert new_dir.exists()
    assert str(new_dir) in r.text


def test_delete_missing_tool_404(client):
    r = client.post("/tools/does-not-exist/delete")
    assert r.status_code == 404


def test_form_actions_use_forwarded_prefix(client):
    # jupyter-server-proxy's generic /proxy/<port> route can serve this
    # page without a trailing slash, which would break plain relative
    # form actions (see PLAN.md). Links must instead be built from
    # X-Forwarded-Prefix so they're correct however the page was reached.
    prefix = "/user/alice/proxy/8766"
    r = client.get("/", headers={"X-Forwarded-Prefix": prefix})
    assert f'action="{prefix}/tools/upload"' in r.text
    assert f'action="{prefix}/config/shared-dir"' in r.text


def test_form_actions_without_proxy_prefix_are_root_relative(client):
    r = client.get("/")
    assert 'action="/tools/upload"' in r.text
    assert 'action="/config/shared-dir"' in r.text


def test_login_page_loads(anon_client):
    r = anon_client.get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_unauthenticated_html_routes_redirect_to_login(anon_client, tmp_path):
    assert anon_client.get("/", follow_redirects=False).headers["location"] == "/login"

    data = _archive_bytes(tmp_path)
    r = anon_client.post(
        "/tools/upload",
        files={"archive": ("greet.zip", data, "application/zip")},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/login"

    r = anon_client.post("/tools/does-not-exist/delete", follow_redirects=False)
    assert r.headers["location"] == "/login"

    r = anon_client.post("/config/shared-dir", data={"path": str(tmp_path)}, follow_redirects=False)
    assert r.headers["location"] == "/login"


def test_login_wrong_password_redirects_with_error(anon_client):
    r = anon_client.post(
        "/login",
        data={"username": ADMIN_USER, "password": "wrong-password"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/login?error=1"


def test_login_unknown_user_rejected(anon_client):
    r = anon_client.post(
        "/login",
        data={"username": "mallory", "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/login?error=1"


def test_logout_clears_session(client):
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_api_requires_basic_auth_not_session_cookie(client):
    # The signed-in HTML session cookie should NOT grant access to the
    # Basic-Auth-gated JSON API — they're deliberately separate gates
    # (see PLAN.md: humans get a login page, curl callers get Basic Auth).
    r = client.get("/api/tools")
    assert r.status_code == 401


def test_api_rejects_wrong_password(anon_client):
    r = anon_client.get("/api/tools", auth=(ADMIN_USER, "wrong-password"))
    assert r.status_code == 401


def test_api_rejects_unknown_user(anon_client):
    r = anon_client.get("/api/tools", auth=("mallory", ADMIN_PASSWORD))
    assert r.status_code == 401

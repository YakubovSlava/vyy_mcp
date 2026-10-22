import importlib
import subprocess
import zipfile

import pytest
from fastapi.testclient import TestClient

from jupyter_mcp_tools.common import auth
from jupyter_mcp_tools.tests.conftest import VALID_MODULE

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


def _archive_bytes(tmp_path, module=VALID_MODULE):
    path = tmp_path / "upload.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("tool.py", module)
    return path.read_bytes()


def test_index_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Навыки пока не установлены" in r.text


def test_upload_install_list_and_delete(client, tmp_path):
    data = _archive_bytes(tmp_path)
    r = client.post(
        "/tools/upload",
        files={"archive": ("greet.zip", data, "application/zip")},
    )
    assert r.status_code == 200
    assert "«greet»" in r.text

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
    assert "Ошибка:" in r.text


def test_set_shared_dir(client, tmp_path):
    new_dir = tmp_path / "other_shared"
    r = client.post("/config/shared-dir", data={"path": str(new_dir)})
    assert r.status_code == 200
    assert new_dir.exists()
    assert str(new_dir) in r.text


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def test_set_shared_dir_keeps_git_password_when_left_blank(client, tmp_path):
    client.post(
        "/config/shared-dir",
        data={"path": str(tmp_path / "shared"), "git_push_username": "bob", "git_push_password": "s3cr3t"},
    )
    # Resubmitting without retyping the password must not clear it --
    # the form never echoes the stored password back (see _render()).
    client.post("/config/shared-dir", data={"path": str(tmp_path / "shared")})

    from jupyter_mcp_tools.common.config import load_config

    config = load_config()
    assert config.git_push_username == "bob"
    assert config.git_push_password == "s3cr3t"


def test_upload_pushes_to_git_when_configured(client, tmp_path):
    # A bare "remote" plus a working clone of it -- local file:// transport
    # needs no real credentials, so this exercises the actual upload ->
    # install -> git add/commit/push chain end to end.
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _run("git", "init", "--bare", "-q", cwd=bare)

    shared = tmp_path / "shared_clone"
    _run("git", "clone", "-q", str(bare), str(shared), cwd=tmp_path)
    _run("git", "config", "user.email", "test@example.com", cwd=shared)
    _run("git", "config", "user.name", "Test", cwd=shared)
    (shared / "README.md").write_text("init\n")
    _run("git", "add", "-A", cwd=shared)
    _run("git", "commit", "-q", "-m", "init", cwd=shared)
    _run("git", "push", "-q", "origin", "HEAD", cwd=shared)

    client.post(
        "/config/shared-dir",
        data={"path": str(shared), "git_push_username": "x", "git_push_password": "y"},
    )

    data = _archive_bytes(tmp_path)
    r = client.post("/tools/upload", files={"archive": ("greet.zip", data, "application/zip")})
    assert "установлен" in r.text
    assert "git push" not in r.text  # no "внимание: git push не выполнен" warning

    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=bare, capture_output=True, text=True, check=True
    ).stdout
    assert "greet" in log


def test_delete_missing_tool_shows_friendly_error(client):
    # Matches every other HTML route's convention (PLAN.md: "HTML routes
    # return 200 with an error banner in the body") rather than leaking a
    # bare FastAPI {"detail": ...} JSON page — this is what a double
    # click, or a stale page after someone else already removed it,
    # actually looks like to the admin.
    r = client.post("/tools/does-not-exist/delete")
    assert r.status_code == 200
    assert "не установлен" in r.text


def test_form_actions_are_relative(client):
    # Relative (no leading "/") form actions resolve correctly under any
    # external mount prefix without depending on a proxy header, as long
    # as the current page ends in "/" — see the trailing-slash
    # self-correct script in templates/base.html and PLAN.md.
    r = client.get("/")
    assert 'action="tools/upload"' in r.text
    assert 'action="config/shared-dir"' in r.text


def test_trailing_slash_never_triggers_a_broken_redirect(client, tmp_path):
    # Starlette's own `redirect_slashes` issues an *absolute* Location
    # (scheme+host+path) with zero proxy-prefix awareness — observed in
    # production as a proxied upload getting redirected straight to the
    # domain root, dropping the whole /user/<name>/proxy/<port> prefix.
    # It must be disabled, and StripTrailingSlashMiddleware must handle
    # the slash itself, so a proxy that happens to forward one never
    # triggers *that* redirect (our own relative one below is fine).
    data = _archive_bytes(tmp_path)
    r = client.post(
        "/tools/upload/",
        files={"archive": ("greet.zip", data, "application/zip")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith(".")  # relative, never absolute

    r = client.get("/users/", follow_redirects=False)
    assert r.status_code == 200

    r = client.get("/docs/", follow_redirects=False)
    assert r.status_code == 200


def test_post_routes_redirect_to_root_with_banner(client, tmp_path):
    # Mutating POSTs (upload/delete/save-config) redirect back to the
    # Skills page itself with the outcome as a query param, per explicit
    # request, rather than rendering index.html inline at the POST's own
    # URL. That inline rendering used to leave the trailing-slash
    # self-correct script (meant only for GET /) running against a URL
    # that never ends in "/", which it then "corrected" with its own GET
    # to that path + "/" — 405ing, since these are POST-only routes.
    # Reported in production as delete/upload/save redirecting to a
    # "Method Not Allowed" page.
    r = client.post("/tools/does-not-exist/delete", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(".")

    r = client.post("/tools/does-not-exist/delete")  # follows the redirect
    assert "не установлен" in r.text
    assert "location.replace" in r.text  # back on the real root page

    data = _archive_bytes(tmp_path)
    r = client.post(
        "/tools/upload", files={"archive": ("greet.zip", data, "application/zip")}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith(".")

    r = client.post("/config/shared-dir", data={"path": str(tmp_path)}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(".")


def test_login_page_loads(anon_client):
    r = anon_client.get("/login")
    assert r.status_code == 200
    assert "Войти" in r.text


def test_login_page_itself_has_no_self_correct_script(anon_client):
    # A normal GET /login visit must NOT get the trailing-slash
    # self-correct script: "/login" already has a real segment of its
    # own, so that script would wrongly try to turn it into "/login/",
    # which 404s. Only the root-rendered-as-login fallback above needs
    # it (test_login_page_loads's self_correct=True path).
    r = anon_client.get("/login")
    assert "location.replace" not in r.text


def test_unauthenticated_html_routes_redirect_to_login(anon_client, tmp_path):
    # Redirect targets are relative to the request path's depth (see
    # _relative_redirect in admin_ui/main.py) — correct regardless of any
    # external mount prefix, since no proxy needs to rewrite them.
    # Root has no segment of its own to anchor a relative redirect on
    # (see PLAN.md / main.py's _redirect_to_login), so it renders the
    # login page directly (200, with its self-correct script) instead
    # of issuing a redirect at all.
    r = anon_client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "Войти" in r.text
    assert "location.replace" in r.text

    data = _archive_bytes(tmp_path)
    r = anon_client.post(
        "/tools/upload",
        files={"archive": ("greet.zip", data, "application/zip")},
        follow_redirects=False,
    )
    assert r.headers["location"] == "../login"

    r = anon_client.post("/tools/does-not-exist/delete", follow_redirects=False)
    assert r.headers["location"] == "../../login"

    r = anon_client.post("/config/shared-dir", data={"path": str(tmp_path)}, follow_redirects=False)
    assert r.headers["location"] == "../login"


def test_login_wrong_password_redirects_with_error(anon_client):
    r = anon_client.post(
        "/login",
        data={"username": ADMIN_USER, "password": "wrong-password"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "login?error=1"


def test_login_unknown_user_rejected(anon_client):
    r = anon_client.post(
        "/login",
        data={"username": "mallory", "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert r.headers["location"] == "login?error=1"


def test_logout_clears_session(client):
    assert client.get("/").status_code == 200
    client.post("/logout")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "Войти" in r.text


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


def _doc_skill_archive_bytes(tmp_path, skill_md="# Doc skill\n\nBody.\n"):
    path = tmp_path / "doc_upload.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("SKILL.md", skill_md)
    return path.read_bytes()


def test_edit_doc_skill_description(client, tmp_path):
    data = _doc_skill_archive_bytes(tmp_path)
    client.post(
        "/tools/upload",
        files={"archive": ("doc.zip", data, "application/zip")},
        data={"name": "doc-skill", "description": "Original description."},
    )

    r = client.post(
        "/tools/doc-skill/edit-description", data={"description": "Updated description.", "group": "networking"}
    )
    assert "обновлён" in r.text

    from jupyter_mcp_tools.common.config import load_config
    from jupyter_mcp_tools.common.registry import list_tools

    tools = list_tools(load_config().shared_dir_path)
    assert tools[0].description == "Updated description."
    assert tools[0].group == "networking"


def test_edit_button_data_attributes_survive_quotes_in_description(client, tmp_path):
    # Regression test: the edit button used to build its onclick handler
    # via `{{ description | tojson }}` interpolated directly into a
    # double-quoted HTML attribute -- tojson doesn't HTML-escape its
    # output, so a description containing a literal '"' broke the
    # attribute (and the handler) outright. Using data-* attributes
    # (HTML-escaped by Jinja like any other interpolated value) instead
    # must keep the button's onclick handler intact and the quotes
    # recoverable via .dataset in JS regardless of what's in the text.
    data = _doc_skill_archive_bytes(tmp_path)
    client.post(
        "/tools/upload",
        files={"archive": ("doc.zip", data, "application/zip")},
        data={"name": "doc-skill", "description": 'It\'s a "tricky" description'},
    )

    r = client.get("/")
    assert 'onclick="openEditDescription(this)"' in r.text
    assert "data-name=\"doc-skill\"" in r.text
    assert "&#34;tricky&#34;" in r.text or "&#x27;" in r.text or "&#39;" in r.text


def test_edit_description_rejects_code_skill(client, tmp_path):
    data = _archive_bytes(tmp_path)
    client.post("/tools/upload", files={"archive": ("greet.zip", data, "application/zip")})

    r = client.post("/tools/greet/edit-description", data={"description": "x"})
    assert "не является" in r.text or "not a doc skill" in r.text.lower()


def test_edit_description_missing_skill_shows_error(client):
    r = client.post("/tools/does-not-exist/edit-description", data={"description": "x"})
    assert "не установлен" in r.text


def test_delete_pushes_to_git_when_configured(client, tmp_path):
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _run("git", "init", "--bare", "-q", cwd=bare)

    shared = tmp_path / "shared_clone"
    _run("git", "clone", "-q", str(bare), str(shared), cwd=tmp_path)
    _run("git", "config", "user.email", "test@example.com", cwd=shared)
    _run("git", "config", "user.name", "Test", cwd=shared)
    (shared / "README.md").write_text("init\n")
    _run("git", "add", "-A", cwd=shared)
    _run("git", "commit", "-q", "-m", "init", cwd=shared)
    _run("git", "push", "-q", "origin", "HEAD", cwd=shared)

    client.post(
        "/config/shared-dir",
        data={"path": str(shared), "git_push_username": "x", "git_push_password": "y"},
    )

    data = _archive_bytes(tmp_path)
    client.post("/tools/upload", files={"archive": ("greet.zip", data, "application/zip")})
    r = client.post("/tools/greet/delete")
    assert "удалён" in r.text
    assert "git push" not in r.text

    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=bare, capture_output=True, text=True, check=True
    ).stdout
    assert "Remove skill" in log


@pytest.fixture
def read_only_client(tmp_path, monkeypatch):
    # Separate app instance with MCP_TOOLS_READ_ONLY=1, mirroring
    # app_module/client above but reloaded with the extra env var set
    # before import, since READ_ONLY is read once at module level.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MCP_TOOLS_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("MCP_TOOLS_ADMINS_PATH", str(tmp_path / "admins.json"))
    monkeypatch.setenv("MCP_TOOLS_SESSION_SECRET_PATH", str(tmp_path / "session_secret"))
    monkeypatch.delenv("MCP_TOOLS_MAIN_HOST", raising=False)
    monkeypatch.setenv("MCP_TOOLS_READ_ONLY", "1")
    auth.add_admin(ADMIN_USER, ADMIN_PASSWORD)

    from jupyter_mcp_tools.admin_ui import main as admin_main

    importlib.reload(admin_main)
    test_client = TestClient(admin_main.app)
    test_client.post("/login", data={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    return test_client


def test_read_only_blocks_upload(read_only_client, tmp_path):
    data = _archive_bytes(tmp_path)
    r = read_only_client.post("/tools/upload", files={"archive": ("greet.zip", data, "application/zip")})
    assert "только просмотр" in r.text.lower()

    r = read_only_client.get("/api/tools", auth=(ADMIN_USER, ADMIN_PASSWORD))
    assert r.json() == []  # nothing actually got installed


def test_read_only_blocks_delete(read_only_client, tmp_path):
    r = read_only_client.post("/tools/does-not-exist/delete")
    assert "только просмотр" in r.text.lower()


def test_read_only_blocks_edit_description(read_only_client):
    r = read_only_client.post("/tools/does-not-exist/edit-description", data={"description": "x"})
    assert "только просмотр" in r.text.lower()


def test_read_only_hides_mutating_buttons(read_only_client):
    r = read_only_client.get("/")
    assert "Только просмотр" in r.text
    assert 'onclick="addSkillDialog.showModal()"' not in r.text
    assert "Удалить" not in r.text


def test_read_only_json_api_returns_403(read_only_client, tmp_path):
    data = _archive_bytes(tmp_path)
    r = read_only_client.post(
        "/api/tools",
        auth=(ADMIN_USER, ADMIN_PASSWORD),
        files={"archive": ("greet.zip", data, "application/zip")},
    )
    assert r.status_code == 403

    r = read_only_client.post("/api/tools/does-not-exist/delete", auth=(ADMIN_USER, ADMIN_PASSWORD))
    assert r.status_code == 403


def test_create_doc_skill_from_text(client):
    r = client.post(
        "/tools/create-doc-skill",
        data={"name": "my-skill", "description": "Use this when...", "body": "# My Skill\n\nDo the thing."},
    )
    assert "создан и установлен" in r.text
    assert "my-skill" in r.text

    r = client.get("/api/tools", auth=(ADMIN_USER, ADMIN_PASSWORD))
    names = [t["name"] for t in r.json()]
    assert names == ["my-skill"]


def test_create_doc_skill_duplicate_without_overwrite_shows_error(client):
    client.post(
        "/tools/create-doc-skill", data={"name": "my-skill", "description": "desc", "body": "body text"}
    )
    r = client.post(
        "/tools/create-doc-skill", data={"name": "my-skill", "description": "desc2", "body": "body text 2"}
    )
    assert "already installed" in r.text


def test_create_doc_skill_blocked_in_read_only_mode(read_only_client):
    r = read_only_client.post(
        "/tools/create-doc-skill",
        data={"name": "my-skill", "description": "desc", "body": "body text"},
    )
    assert "только просмотр" in r.text.lower()

    r = read_only_client.get("/api/tools", auth=(ADMIN_USER, ADMIN_PASSWORD))
    assert r.json() == []


def test_api_create_doc_skill_returns_403_in_read_only_mode(read_only_client):
    r = read_only_client.post(
        "/api/tools/create-doc-skill",
        auth=(ADMIN_USER, ADMIN_PASSWORD),
        data={"name": "my-skill", "description": "desc", "body": "body text"},
    )
    assert r.status_code == 403


def test_upload_accepts_archive_with_wrapping_folder(client, tmp_path):
    # A zip of a folder (e.g. file-manager "Compress") nests content one
    # level deep -- "mytool/tool.py" instead of "tool.py" at the root.
    archive = tmp_path / "wrapped.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mytool/tool.py", VALID_MODULE)

    r = client.post("/tools/upload", files={"archive": ("wrapped.zip", archive.read_bytes(), "application/zip")})
    assert "«greet»" in r.text

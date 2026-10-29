"""The "Файлы" tab: routes that upload, edit and delete repo files.

Same split as elsewhere: the path-safety rules live in
`test_repo_files.py`, this file checks the HTTP layer — auth, read-only
mode, redirects, and that a mutation actually reaches git.
"""
import subprocess
import zipfile
import io

import pytest

from jupyter_mcp_tools.common.config import load_config
from jupyter_mcp_tools.tests.test_admin_api import (  # noqa: F401
    anon_client as admin_anon_client,
    app_module,  # noqa: F401
    client as admin_client,
    read_only_client,
)


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, admin_client):
    """A shared dir that is also a working clone of a bare remote."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _run("git", "init", "--bare", "-q", cwd=bare)

    clone = tmp_path / "clone"
    _run("git", "clone", "-q", str(bare), str(clone), cwd=tmp_path)
    _run("git", "config", "user.email", "t@example.com", cwd=clone)
    _run("git", "config", "user.name", "T", cwd=clone)
    (clone / "tools" / "echo").mkdir(parents=True)
    (clone / "tools" / "echo" / "tool.py").write_text("def echo(): pass\n")
    (clone / "README.md").write_text("# init\n")
    _run("git", "add", "-A", cwd=clone)
    _run("git", "commit", "-q", "-m", "init", cwd=clone)
    _run("git", "push", "-q", "origin", "HEAD", cwd=clone)

    admin_client.post(
        "/config/shared-dir",
        data={"path": str(clone), "git_push_username": "x", "git_push_password": "y"},
    )
    return bare, clone


def _zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------


def test_files_page_lists_repo_files_and_hides_tools(admin_client, repo):
    _bare, clone = repo
    (clone / "src").mkdir()
    (clone / "src" / "app.py").write_text("print(1)\n")
    r = admin_client.get("/files")
    assert r.status_code == 200
    assert "src/app.py" in r.text
    # The registry's own directory must not be offered for editing here.
    assert "tools/echo/tool.py" not in r.text


def test_files_tab_is_in_the_nav(admin_client):
    assert 'href="files"' in admin_client.get("/").text


def test_files_page_requires_login(admin_anon_client):
    r = admin_anon_client.get("/files", follow_redirects=False)
    assert r.status_code in (303, 302, 307)


# --------------------------------------------------------------------------
# upload
# --------------------------------------------------------------------------


def test_upload_unpacks_and_pushes(admin_client, repo):
    bare, clone = repo
    payload = _zip_bytes({"src/run.sh": b"#!/bin/sh\n", "docs/notes.md": b"# hey\n"})
    r = admin_client.post(
        "/files/upload", files={"archive": ("src.zip", payload, "application/zip")}
    )
    assert "Загружено файлов: 2" in r.text
    assert (clone / "src" / "run.sh").read_text() == "#!/bin/sh\n"

    pushed = subprocess.run(
        ["git", "show", "HEAD:src/run.sh"], cwd=bare, capture_output=True, text=True, check=True
    ).stdout
    assert pushed == "#!/bin/sh\n"


def test_upload_refuses_to_clobber_without_overwrite(admin_client, repo):
    _bare, clone = repo
    payload = _zip_bytes({"README.md": b"# clobbered\n"})
    r = admin_client.post("/files/upload", files={"archive": ("a.zip", payload, "application/zip")})
    assert "уже существует" in r.text
    assert (clone / "README.md").read_text() == "# init\n"


def test_upload_with_overwrite_replaces(admin_client, repo):
    _bare, clone = repo
    payload = _zip_bytes({"README.md": b"# replaced\n"})
    r = admin_client.post(
        "/files/upload",
        files={"archive": ("a.zip", payload, "application/zip")},
        data={"overwrite": "1"},
    )
    assert "Загружено файлов: 1" in r.text
    assert (clone / "README.md").read_text() == "# replaced\n"


def test_upload_cannot_touch_tools(admin_client, repo):
    _bare, clone = repo
    payload = _zip_bytes({"tools/evil/tool.py": b"def evil(): pass\n"})
    r = admin_client.post(
        "/files/upload",
        files={"archive": ("a.zip", payload, "application/zip")},
        data={"overwrite": "1"},
    )
    assert "служебная папка" in r.text
    assert not (clone / "tools" / "evil").exists()


def test_upload_blocked_in_read_only_mode(read_only_client):
    payload = _zip_bytes({"x.txt": b"x"})
    r = read_only_client.post("/files/upload", files={"archive": ("a.zip", payload, "application/zip")})
    assert "только просмотр" in r.text.lower()


# --------------------------------------------------------------------------
# edit
# --------------------------------------------------------------------------


def test_new_file_is_created_and_pushed(admin_client, repo):
    bare, clone = repo
    r = admin_client.post("/files/save", data={"path": "scripts/run.sh", "content": "#!/bin/sh\n"})
    assert "сохранён" in r.text
    assert (clone / "scripts" / "run.sh").read_text() == "#!/bin/sh\n"
    pushed = subprocess.run(
        ["git", "show", "HEAD:scripts/run.sh"], cwd=bare, capture_output=True, text=True, check=True
    ).stdout
    assert pushed == "#!/bin/sh\n"


def test_editing_an_existing_file_replaces_content(admin_client, repo):
    _bare, clone = repo
    admin_client.post("/files/save", data={"path": "README.md", "content": "# edited\n"})
    assert (clone / "README.md").read_text() == "# edited\n"


def test_save_refuses_a_protected_path(admin_client, repo):
    _bare, clone = repo
    r = admin_client.post("/files/save", data={"path": "tools/echo/tool.py", "content": "hacked"})
    assert "служебная папка" in r.text
    assert "hacked" not in (clone / "tools" / "echo" / "tool.py").read_text()


def test_save_refuses_path_traversal(admin_client, repo, tmp_path):
    _bare, _clone = repo
    r = admin_client.post("/files/save", data={"path": "../escaped.txt", "content": "x"})
    assert "Ошибка" in r.text
    assert not (tmp_path / "escaped.txt").exists()


def test_raw_endpoint_returns_contents_for_the_editor(admin_client, repo):
    _bare, clone = repo
    (clone / "notes.md").write_text("# notes\n")
    r = admin_client.get("/files/raw", params={"path": "notes.md"})
    assert r.status_code == 200
    assert r.text == "# notes\n"


def test_raw_endpoint_rejects_protected_paths(admin_client, repo):
    r = admin_client.get("/files/raw", params={"path": "tools/echo/tool.py"})
    assert r.status_code == 400


def test_download_serves_binaries(admin_client, repo):
    _bare, clone = repo
    (clone / "logo.png").write_bytes(b"\x89PNG\x00binary")
    r = admin_client.get("/files/download", params={"path": "logo.png"})
    assert r.status_code == 200
    assert r.content == b"\x89PNG\x00binary"


def test_download_missing_file_is_404(admin_client, repo):
    r = admin_client.get("/files/download", params={"path": "nope.bin"})
    assert r.status_code == 404


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------


def test_delete_removes_file_and_pushes(admin_client, repo):
    bare, clone = repo
    (clone / "obsolete.txt").write_text("bye\n")
    r = admin_client.post("/files/delete", data={"path": "obsolete.txt"})
    assert "удалён" in r.text
    assert not (clone / "obsolete.txt").exists()
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=bare, capture_output=True, text=True, check=True,
    ).stdout
    assert "obsolete.txt" not in tree


def test_delete_refuses_tools(admin_client, repo):
    _bare, clone = repo
    r = admin_client.post("/files/delete", data={"path": "tools/echo"})
    assert "служебная папка" in r.text
    assert (clone / "tools" / "echo" / "tool.py").exists()


def test_delete_blocked_in_read_only_mode(read_only_client):
    r = read_only_client.post("/files/delete", data={"path": "x.txt"})
    assert "только просмотр" in r.text.lower()


# --------------------------------------------------------------------------
# nothing machine-generated any more
# --------------------------------------------------------------------------


def test_a_push_writes_the_launcher_but_no_run_script_or_vendor_dir(admin_client, repo):
    """The only machine-generated thing left, by explicit request, is the
    `launcher/` folder. The old root-level `run.sh` and `_vendor/` stay gone."""
    bare, clone = repo
    admin_client.post("/files/save", data={"path": "some.txt", "content": "x\n"})
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=bare, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert "launcher/requirements.txt" in tree
    assert "launcher/src/main.py" in tree
    assert "run.sh" not in tree
    assert not any(path.startswith("_vendor/") for path in tree)

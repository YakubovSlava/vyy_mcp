"""End-to-end test of the child-admin relay against a real main host.

Spins up the admin UI as an actual `uvicorn` subprocess (a real TCP
socket, not an in-process ASGI transport) acting as the main host, then
drives a "child" instance of the same app (in-process, configured via
MCP_TOOLS_MAIN_HOST) through its HTML routes. The child's routes shell
out to the real `curl` binary against the real subprocess, so this
exercises the full main<->child communication path, not just the Python
call graph.
"""
from __future__ import annotations

import importlib
import io
import os
import socket
import subprocess
import sys
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from jupyter_mcp_tools.common import auth
from jupyter_mcp_tools.tests.conftest import VALID_MODULE


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def main_host(tmp_path, monkeypatch):
    port = _free_port()
    home = tmp_path / "main_home"
    home.mkdir()
    admins_path = home / "admins.json"

    env = {
        **os.environ,
        "HOME": str(home),
        "MCP_TOOLS_CONFIG_PATH": str(home / "config.json"),
        "MCP_TOOLS_ADMINS_PATH": str(admins_path),
    }
    env.pop("MCP_TOOLS_MAIN_HOST", None)

    monkeypatch.setenv("MCP_TOOLS_ADMINS_PATH", str(admins_path))
    auth.add_admin("alice", "s3cret")

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "jupyter_mcp_tools.admin_ui.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("main host subprocess never started listening")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def child_client(main_host, monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_TOOLS_MAIN_HOST", main_host)
    monkeypatch.setenv("MCP_TOOLS_ADMIN_USER", "alice")
    monkeypatch.setenv("MCP_TOOLS_ADMIN_PASSWORD", "s3cret")

    from jupyter_mcp_tools.admin_ui import main as admin_main

    importlib.reload(admin_main)
    return TestClient(admin_main.app)


def _archive_bytes(tmp_path, module=VALID_MODULE):
    path = tmp_path / "upload.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("tool.py", module)
    return path.read_bytes()


def test_child_relay_lists_empty_main_host(child_client):
    r = child_client.get("/")
    assert r.status_code == 200
    assert "Навыки пока не установлены" in r.text
    assert "Error:" not in r.text


def test_child_relay_rejects_bad_credentials(main_host, monkeypatch):
    monkeypatch.setenv("MCP_TOOLS_MAIN_HOST", main_host)
    monkeypatch.setenv("MCP_TOOLS_ADMIN_USER", "alice")
    monkeypatch.setenv("MCP_TOOLS_ADMIN_PASSWORD", "wrong-password")

    from jupyter_mcp_tools.admin_ui import main as admin_main

    importlib.reload(admin_main)
    client = TestClient(admin_main.app)

    r = client.get("/")
    assert r.status_code == 200
    assert "rejected these credentials" in r.text


def test_child_relay_upload_and_delete_round_trip(child_client, tmp_path):
    data = _archive_bytes(tmp_path)
    r = child_client.post("/tools/upload", files={"archive": ("greet.zip", data, "application/zip")})
    assert r.status_code == 200
    assert "установлен" in r.text

    r = child_client.get("/")
    assert "greet" in r.text

    r = child_client.post("/tools/greet/delete")
    assert r.status_code == 200
    assert "удалён" in r.text

    r = child_client.get("/")
    assert "Навыки пока не установлены" in r.text


def test_child_relay_download_round_trip(child_client, tmp_path):
    data = _archive_bytes(tmp_path)
    r = child_client.post("/tools/upload", files={"archive": ("greet.zip", data, "application/zip")})
    assert r.status_code == 200

    r = child_client.get("/tools/greet/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist() == ["tool.py"]


def test_child_relay_download_missing_skill_returns_404(child_client):
    r = child_client.get("/tools/nope/download")
    assert r.status_code == 404


def test_child_relay_address_dialog_shows_main_host_readonly(child_client, main_host):
    r = child_client.get("/")
    assert main_host in r.text
    assert 'action="/config/shared-dir"' not in r.text


def test_child_relay_cannot_change_shared_dir(child_client, tmp_path):
    r = child_client.post("/config/shared-dir", data={"path": str(tmp_path)})
    assert r.status_code == 403


def test_child_relay_cannot_reset_instructions(child_client):
    r = child_client.post("/config/instructions/reset")
    assert r.status_code == 403


def test_child_relay_has_no_users_page(child_client):
    # A child relay has no local users of its own to show — the "Users"
    # tab only exists on the main host.
    r = child_client.get("/users")
    assert r.status_code == 404


def test_child_relay_has_no_docs_page(child_client):
    # Same reasoning — there's no local shared directory to document a
    # path for on a child relay.
    r = child_client.get("/docs")
    assert r.status_code == 404
    assert 'href="users"' not in child_client.get("/").text


def test_child_relay_create_doc_skill_round_trip(child_client):
    r = child_client.post(
        "/tools/create-doc-skill",
        data={"name": "my-skill", "description": "Use this when...", "body": "# My Skill\n\nDo the thing."},
    )
    assert r.status_code == 200
    assert "создан и установлен" in r.text

    r = child_client.get("/")
    assert "my-skill" in r.text


def test_child_relay_group_create_list_delete_round_trip(child_client):
    r = child_client.post("/groups", data={"name": "networking", "description": "Network stuff."})
    assert r.status_code == 200
    assert "создана" in r.text

    r = child_client.get("/groups")
    assert "networking" in r.text
    assert "Network stuff." in r.text

    r = child_client.post("/groups/networking/delete")
    assert "удалена" in r.text


def test_child_relay_groups_page_has_no_users_docs_links(child_client):
    r = child_client.get("/groups")
    assert r.status_code == 200
    assert 'href="users"' not in r.text


# --------------------------------------------------------------------------
# Files tab through the relay
# --------------------------------------------------------------------------


def _files_zip(tmp_path, entries):
    path = tmp_path / "files.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return path.read_bytes()


def test_child_relay_files_page_lists_main_hosts_files(child_client, tmp_path):
    child_client.post(
        "/files/upload", files={"archive": ("f.zip", _files_zip(tmp_path, {"notes.md": b"# hi\n"}), "application/zip")}
    )
    r = child_client.get("/files")
    assert r.status_code == 200
    assert "notes.md" in r.text
    # The page exists on the relay now instead of 404ing.
    assert 'href="files"' in child_client.get("/").text


def test_child_relay_file_upload_save_delete_round_trip(child_client, tmp_path):
    payload = _files_zip(tmp_path, {"docs/a.md": b"# a\n"})
    r = child_client.post("/files/upload", files={"archive": ("f.zip", payload, "application/zip")})
    assert "Загружено файлов" in r.text

    r = child_client.get("/files")
    assert "docs/a.md" in r.text

    r = child_client.post("/files/save", data={"path": "docs/a.md", "content": "# edited\n"})
    assert "сохранён" in r.text
    assert "# edited" in child_client.get("/files/raw", params={"path": "docs/a.md"}).text

    r = child_client.post("/files/delete", data={"path": "docs/a.md"})
    assert "удалён" in r.text
    assert "docs/a.md" not in child_client.get("/files").text


def test_child_relay_cannot_touch_tools_via_files(child_client, tmp_path):
    """The reserved-path guard runs on the *main* host, so a relay can't get
    around it by going through its own (weaker) route."""
    payload = _files_zip(tmp_path, {"tools/evil/tool.py": b"def evil(): pass\n"})
    r = child_client.post(
        "/files/upload",
        files={"archive": ("f.zip", payload, "application/zip")},
        data={"overwrite": "1"},
    )
    assert "служебная папка" in r.text


def test_child_relay_file_download_serves_bytes(child_client, tmp_path):
    payload = _files_zip(tmp_path, {"logo.png": b"\x89PNG\x00binary"})
    child_client.post("/files/upload", files={"archive": ("f.zip", payload, "application/zip")})

    r = child_client.get("/files/download", params={"path": "logo.png"})
    assert r.status_code == 200
    assert r.content == b"\x89PNG\x00binary"


def test_child_relay_files_api_parity(child_client, tmp_path):
    """The relay's own /api/files must forward too — it's what another relay
    or a script would call."""
    payload = _files_zip(tmp_path, {"x.txt": b"one\n"})
    child_client.post("/files/upload", files={"archive": ("f.zip", payload, "application/zip")})

    r = child_client.get("/api/files", auth=("alice", "s3cret"))
    assert r.status_code == 200
    assert any(f["path"] == "x.txt" for f in r.json())

    r = child_client.post("/api/files/save", auth=("alice", "s3cret"), data={"path": "x.txt", "content": "two\n"})
    assert r.status_code == 200
    assert child_client.get("/files/raw", params={"path": "x.txt"}).text == "two\n"

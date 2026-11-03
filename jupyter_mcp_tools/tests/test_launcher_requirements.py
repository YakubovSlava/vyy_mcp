"""The admin-editable `launcher/requirements.txt`.

Two things matter: the admin's text lands in the child repo verbatim, and
saving an unrelated form doesn't wipe the setting (the shared-dir route
rebuilds the whole config object, which is exactly how such a field silently
disappears).
"""
import subprocess

import pytest

from jupyter_mcp_tools.common import launcher_bundle
from jupyter_mcp_tools.tests.test_admin_api import (  # noqa: F401
    ADMIN_PASSWORD,
    ADMIN_USER,
    app_module,
    anon_client as admin_anon_client,
    client as admin_client,
    read_only_client,
)

CUSTOM = "mcp>=1.6,<2\nnumpy==2.1.0\n# our additions\npandas>=2\n"


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, admin_client):
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _run("git", "init", "--bare", "-q", cwd=bare)

    clone = tmp_path / "repo"
    _run("git", "clone", "-q", str(bare), str(clone), cwd=tmp_path)
    _run("git", "config", "user.email", "t@example.com", cwd=clone)
    _run("git", "config", "user.name", "T", cwd=clone)
    (clone / "README.md").write_text("init\n")
    _run("git", "add", "-A", cwd=clone)
    _run("git", "commit", "-q", "-m", "init", cwd=clone)
    _run("git", "push", "-q", "origin", "HEAD", cwd=clone)

    admin_client.post(
        "/config/shared-dir",
        data={"path": str(clone), "git_push_username": "x", "git_push_password": "y"},
    )
    return bare, clone


# --------------------------------------------------------------------------
# the text itself
# --------------------------------------------------------------------------


def test_default_is_the_derived_dependency_list():
    text = launcher_bundle.requirements_text()
    assert "mcp" in text and "fastapi" in text
    # Nothing self-referential: sources are copied into launcher/ instead.
    assert "git+" not in text


def test_override_replaces_the_list_verbatim():
    text = launcher_bundle.requirements_text(CUSTOM)
    assert text == CUSTOM  # no merging, no re-sorting


def test_override_gains_a_trailing_newline():
    assert launcher_bundle.requirements_text("numpy==2.1.0").endswith("\n")


@pytest.mark.parametrize("blank", ["", "   ", "\n\n"])
def test_a_blank_override_falls_back_to_the_default(blank):
    """A blank value must not write an empty requirements.txt — that installs
    nothing and leaves a launcher that can't start."""
    text = launcher_bundle.requirements_text(blank)
    assert "mcp" in text


# --------------------------------------------------------------------------
# through the admin UI
# --------------------------------------------------------------------------


def test_saving_writes_the_file_and_pushes_it(admin_client, repo):
    bare, clone = repo
    r = admin_client.post("/config/launcher-requirements", data={"launcher_requirements": CUSTOM})
    assert "сохранены" in r.text

    assert (clone / "launcher" / "requirements.txt").read_text() == CUSTOM
    pushed = subprocess.run(
        ["git", "show", "HEAD:launcher/requirements.txt"],
        cwd=bare, capture_output=True, text=True, check=True,
    ).stdout
    assert pushed == CUSTOM


def test_the_textarea_is_prefilled_with_the_effective_text(admin_client, repo):
    admin_client.post("/config/launcher-requirements", data={"launcher_requirements": CUSTOM})
    html = admin_client.get("/").text
    assert "numpy==2.1.0" in html


def test_saving_an_unrelated_form_keeps_the_override(admin_client, repo):
    """Regression risk: the shared-dir route rebuilds AppConfig from scratch,
    so a field it doesn't copy is silently reset."""
    from jupyter_mcp_tools.common.config import load_config

    admin_client.post("/config/launcher-requirements", data={"launcher_requirements": CUSTOM})
    admin_client.post("/config/shared-dir", data={"path": str(repo[1]), "instructions": "new rules"})

    assert load_config().launcher_requirements == CUSTOM


def test_resetting_rewrites_the_file_back_to_the_default(admin_client, repo):
    """A reset that only cleared the config would leave the old custom pins in
    the repo while the textarea showed the default list."""
    bare, clone = repo
    admin_client.post("/config/launcher-requirements", data={"launcher_requirements": CUSTOM})
    assert "numpy" in (clone / "launcher" / "requirements.txt").read_text()

    r = admin_client.post("/config/launcher-requirements", data={"launcher_requirements": ""})
    assert "сброшен" in r.text

    on_disk = (clone / "launcher" / "requirements.txt").read_text()
    assert "numpy" not in on_disk
    assert "fastapi" in on_disk

    pushed = subprocess.run(
        ["git", "show", "HEAD:launcher/requirements.txt"],
        cwd=bare, capture_output=True, text=True, check=True,
    ).stdout
    assert "numpy" not in pushed


def test_blocked_in_read_only_mode(read_only_client):
    r = read_only_client.post("/config/launcher-requirements", data={"launcher_requirements": CUSTOM})
    assert "только просмотр" in r.text.lower()


def test_a_skill_push_uses_the_override_too(admin_client, repo):
    """The override must apply on every write path, not only the dedicated
    route — otherwise the next skill install silently reverts the file."""
    bare, clone = repo
    admin_client.post("/config/launcher-requirements", data={"launcher_requirements": CUSTOM})

    (clone / "notes.md").write_text("x\n")
    r = admin_client.post("/files/save", data={"path": "notes.md", "content": "y\n"})
    assert "сохранён" in r.text

    pushed = subprocess.run(
        ["git", "show", "HEAD:launcher/requirements.txt"],
        cwd=bare, capture_output=True, text=True, check=True,
    ).stdout
    assert "numpy" in pushed

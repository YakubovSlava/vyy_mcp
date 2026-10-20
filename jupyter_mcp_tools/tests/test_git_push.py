import subprocess

import pytest

from jupyter_mcp_tools.common import git_push
from jupyter_mcp_tools.common.git_push import push_shared_dir


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo_with_remote(tmp_path):
    """A bare 'remote' repo plus a working clone of it — local file://
    transport needs no real credentials, so these tests exercise the
    actual `git add/commit/push` subprocess calls end to end."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _run("git", "init", "--bare", "-q", cwd=bare)

    clone = tmp_path / "clone"
    _run("git", "clone", "-q", str(bare), str(clone), cwd=tmp_path)
    _run("git", "config", "user.email", "test@example.com", cwd=clone)
    _run("git", "config", "user.name", "Test", cwd=clone)
    # A bare repo with no commits yet has no default branch checked out
    # on clone; give it one so the first push has somewhere to go.
    (clone / "README.md").write_text("init\n")
    _run("git", "add", "-A", cwd=clone)
    _run("git", "commit", "-q", "-m", "init", cwd=clone)
    _run("git", "push", "-q", "origin", "HEAD", cwd=clone)

    return bare, clone


def test_push_shared_dir_happy_path(tmp_path):
    bare, clone = _init_repo_with_remote(tmp_path)
    (clone / "tools").mkdir()
    (clone / "tools" / "new_skill.py").write_text("def f():\n    pass\n")

    error = push_shared_dir(clone, username="x", password="y", commit_message="Add skill 'new_skill'")
    assert error is None

    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=bare, capture_output=True, text=True, check=True
    ).stdout
    assert "Add skill" in log


def test_push_shared_dir_nothing_to_commit_is_not_an_error(tmp_path):
    _, clone = _init_repo_with_remote(tmp_path)
    error = push_shared_dir(clone, username="x", password="y", commit_message="no-op")
    assert error is None


def test_push_shared_dir_not_a_git_repo(tmp_path):
    plain_dir = tmp_path / "not_a_repo"
    plain_dir.mkdir()
    error = push_shared_dir(plain_dir, username="x", password="y", commit_message="msg")
    assert error is not None
    assert "git" in error.lower() or "репозитор" in error.lower()


def test_push_shared_dir_push_failure_surfaces_detail(tmp_path):
    _, clone = _init_repo_with_remote(tmp_path)
    # Point origin at a URL that can never succeed, to exercise the
    # push-failed branch specifically (as opposed to commit-failed).
    subprocess.run(
        ["git", "remote", "set-url", "origin", "file:///does/not/exist"],
        cwd=clone,
        check=True,
        capture_output=True,
    )
    (clone / "tools").mkdir()
    (clone / "tools" / "x.py").write_text("def f():\n    pass\n")

    error = push_shared_dir(clone, username="x", password="y", commit_message="msg")
    assert error is not None
    assert "push" in error.lower()


def test_push_overrides_credential_helper_and_askpass(tmp_path, monkeypatch):
    # A local file:// remote never actually invokes credential.helper (no
    # auth needed), so this can't be exercised end to end without a real
    # HTTPS server — this instead asserts the actual subprocess argv for
    # the push step, which is where the override matters: a system-wide
    # gitconfig's own credential.helper takes priority over GIT_ASKPASS
    # and can fail/prompt before our askpass is ever consulted (the
    # reported "terminal prompts disabled" failure), so the push call
    # must force both `-c credential.helper=` and `-c core.askpass=...`
    # itself rather than relying on the env var alone.
    _, clone = _init_repo_with_remote(tmp_path)
    (clone / "tools").mkdir()
    (clone / "tools" / "x.py").write_text("def f():\n    pass\n")

    calls = []
    real_run = subprocess.run

    def spy(cmd, *args, **kwargs):
        calls.append(cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(git_push.subprocess, "run", spy)

    error = push_shared_dir(clone, username="x", password="y", commit_message="msg")
    assert error is None

    push_call = next(c for c in calls if "push" in c)
    assert "-c" in push_call and "credential.helper=" in push_call
    assert any(arg.startswith("core.askpass=") for arg in push_call)

    add_call = next(c for c in calls if "add" in c)
    assert "-c" not in add_call  # only the credentialed (push) step needs the override

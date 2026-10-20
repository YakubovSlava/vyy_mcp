import subprocess
import sys

import pytest

from jupyter_mcp_tools.common.git_push import _run_under_pty_answering_credentials, push_shared_dir


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


def test_push_runs_under_a_pty_without_a_real_prompt(tmp_path):
    # A local file:// remote never prompts for credentials at all, so this
    # can't exercise the actual prompt-answering loop end to end (that
    # needs a real HTTPS server) -- it does confirm the pexpect-based
    # push still completes normally (spawn -> no prompt seen -> EOF ->
    # exit 0) when no prompt ever shows up, which is the common case.
    bare, clone = _init_repo_with_remote(tmp_path)
    (clone / "tools").mkdir()
    (clone / "tools" / "new_skill.py").write_text("def f():\n    pass\n")

    error = push_shared_dir(clone, username="alice", password="s3cret", commit_message="Add skill 'new_skill'")
    assert error is None

    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=bare, capture_output=True, text=True, check=True
    ).stdout
    assert "Add skill" in log


_FAKE_PROMPTER = """\
import sys
print("Username for 'https://example.com': ", end="", flush=True)
user = input()
print("Password for 'https://{}@example.com': ".format(user), end="", flush=True)
password = input()
print(f"received: {user}/{password}")
sys.exit(0)
"""


def test_run_under_pty_answers_real_username_and_password_prompts(tmp_path):
    # A fake script that genuinely prompts for a username then a password
    # at a real terminal (unlike a local file:// git remote, which never
    # prompts at all) -- this is what actually exercises the
    # prompt-answering loop end to end, the mechanism this fell back to
    # after GIT_ASKPASS/credential.helper didn't work against a real
    # corporate Bitbucket Server in production.
    script = tmp_path / "prompter.py"
    script.write_text(_FAKE_PROMPTER)

    error = _run_under_pty_answering_credentials(
        [sys.executable, str(script)], cwd=tmp_path, username="alice", password="s3cret"
    )
    assert error is None


def test_run_under_pty_reports_nonzero_exit(tmp_path):
    script = tmp_path / "fails.py"
    script.write_text("import sys; sys.exit(7)\n")

    error = _run_under_pty_answering_credentials(
        [sys.executable, str(script)], cwd=tmp_path, username="alice", password="s3cret"
    )
    assert error is not None

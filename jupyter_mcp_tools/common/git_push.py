"""Pushes the shared tools directory's git history after a skill is added.

Per explicit request: the shared directory is assumed to already be a git
repository with its remote already configured — this project only runs
`git add`/`commit`/`push` in it; it never creates the repo or touches the
remote configuration itself.

The push step runs under a real pseudo-terminal (via `pexpect`) and types
the admin-configured username/password in response to git's own
"Username for ..."/"Password for ..." prompts, exactly as if someone had
typed them at an interactive terminal. This is a deliberate fallback per
explicit request after `GIT_ASKPASS`/`core.askpass` (see git history)
didn't work against this org's Bitbucket Server — some corporate git
setups apparently don't go through the normal askpass/credential-helper
path at all, so the only thing left that's guaranteed to work the same
way a human's manual `git push` does is emulating that human at a real
terminal.

Pushing is best-effort: a failure here must never block the skill install
that triggered it, only surface as a non-fatal detail in the response.

**Nothing in the shared directory is machine-generated any more.** Earlier
versions wrote a `run.sh`, appended to `.gitignore` and vendored a copy of
this package into it. Per explicit request all of that is gone: everything
at the repo's root except `tools/` is now an ordinary file the admin
uploads, edits and deletes through the admin UI's "Файлы" tab, so a push
commits exactly what the admin put there and this module adds nothing.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

import pexpect

logger = logging.getLogger("jupyter_mcp_tools.git_push")

_TIMEOUT_SECONDS = 30
_PROMPT_RE = re.compile(r"(username|password|логин|пароль)", re.IGNORECASE)

def _run(shared_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(shared_dir), *args],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
    )


def _run_under_pty_answering_credentials(
    argv: list[str], *, cwd: Path, username: str, password: str, extra_env: dict[str, str] | None = None
) -> str | None:
    """Run `argv` attached to a pseudo-terminal and type `username`/
    `password` in response to whatever "Username"/"Password" prompts it
    shows, the same way a human at a real terminal would. Returns an
    error string on failure, `None` on success (including when no prompt
    ever showed up at all)."""
    env = {**os.environ, **(extra_env or {})}
    try:
        child = pexpect.spawn(
            argv[0],
            argv[1:],
            cwd=str(cwd),
            env=env,
            timeout=_TIMEOUT_SECONDS,
            encoding="utf-8",
            codec_errors="replace",
        )
    except pexpect.exceptions.ExceptionPexpect as exc:
        return f"{argv[0]}: не удалось запустить псевдотерминал ({exc})"

    try:
        answers_sent = 0
        while answers_sent < 2:
            index = child.expect([_PROMPT_RE, pexpect.EOF, pexpect.TIMEOUT])
            if index != 0:
                break
            prompt = child.after.lower()
            if "pass" in prompt or "парол" in prompt:
                child.sendline(password)
            else:
                child.sendline(username)
            answers_sent += 1
        child.expect(pexpect.EOF, timeout=_TIMEOUT_SECONDS)
    except pexpect.exceptions.TIMEOUT:
        output = (child.before or "").strip()
        child.close(force=True)
        logger.warning("%s timed out waiting for a prompt in %s: %s", argv[0], cwd, output)
        return f"{argv[0]}: таймаут при ожидании ответа ({output[-300:]})" if output else f"{argv[0]}: таймаут"

    output = (child.before or "").strip()
    child.close()
    if child.exitstatus not in (0, None):
        logger.warning("%s failed in %s: %s", argv[0], cwd, output)
        return f"{argv[0]}: {output[-500:]}"
    return None


def push_shared_dir(shared_dir: Path, *, username: str, password: str, commit_message: str) -> str | None:
    """Add + commit + push whatever changed in `shared_dir`'s git repo.

    Returns `None` on success, or a short human-readable error string on
    failure (never raises).
    """
    if not (shared_dir / ".git").exists():
        return "общая директория не является git-репозиторием"


    try:
        _run(shared_dir, "add", "-A")
        # `git commit` exits 1 (indistinguishable from a real failure by
        # exit code alone) when there's nothing staged, and the message
        # saying so is locale-dependent -- checking for staged changes
        # first avoids parsing that text in any language.
        nothing_staged = _run(shared_dir, "diff", "--cached", "--quiet").returncode == 0
        if not nothing_staged:
            commit = _run(shared_dir, "commit", "-m", commit_message)
            if commit.returncode != 0:
                detail = (commit.stderr or commit.stdout).strip()
                logger.warning("git commit failed in %s: %s", shared_dir, detail)
                return f"git commit: {detail}"
    except subprocess.TimeoutExpired:
        logger.warning("git add/commit timed out in %s", shared_dir)
        return "git commit: превышено время ожидания"
    except OSError as exc:
        logger.warning("git add/commit could not run in %s: %s", shared_dir, exc)
        return f"git commit: {exc}"

    return _run_under_pty_answering_credentials(
        ["git", "-C", str(shared_dir), "push"],
        cwd=shared_dir,
        username=username,
        password=password,
        extra_env={"GIT_TERMINAL_PROMPT": "1"},
    )

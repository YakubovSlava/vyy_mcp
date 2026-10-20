"""Pushes the shared tools directory's git history after a skill is added.

Per explicit request: the shared directory is assumed to already be a git
repository with its remote already configured — this project only runs
`git add`/`commit`/`push` in it; it never creates the repo or touches the
remote configuration itself. Credentials (plain HTTPS username/password)
are supplied by the main admin via the settings dialog ("⚙" on the Skills
page) and passed to `git push` through `GIT_ASKPASS`
(`scripts/git_askpass.py`) rather than embedded in the push command/URL,
so they never show up in `ps aux` output — only in this process's own
environment, same exposure level as any other env-var-held secret.

Pushing is best-effort: a failure here must never block the skill install
that triggered it, only surface as a non-fatal detail in the response.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger("jupyter_mcp_tools.git_push")

ASKPASS_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "git_askpass.py"

_TIMEOUT_SECONDS = 30


def push_shared_dir(shared_dir: Path, *, username: str, password: str, commit_message: str) -> str | None:
    """Add + commit + push whatever changed in `shared_dir`'s git repo.

    Returns `None` on success, or a short human-readable error string on
    failure (never raises).
    """
    if not (shared_dir / ".git").exists():
        return "общая директория не является git-репозиторием"

    env = {
        **os.environ,
        "GIT_ASKPASS": str(ASKPASS_SCRIPT),
        "GIT_PUSH_USERNAME": username,
        "GIT_PUSH_PASSWORD": password,
        "GIT_TERMINAL_PROMPT": "0",
    }

    def run(*args: str, credentialed: bool = False) -> subprocess.CompletedProcess:
        # `credentialed=True` (the push itself) explicitly overrides
        # `credential.helper` to nothing and `core.askpass` to our script
        # via `-c`, rather than relying on the GIT_ASKPASS env var alone —
        # a system-wide gitconfig with its own credential.helper (git
        # credential manager, `cache`, `store`, ...) takes priority over
        # GIT_ASKPASS and can fail/prompt on its own before our askpass
        # is ever consulted, which is exactly the "terminal prompts
        # disabled" failure this is fixing. `-c` options win over any
        # config file, so this is the one override guaranteed to apply
        # regardless of what's already configured on the host.
        cfg = ["-c", "credential.helper=", "-c", f"core.askpass={ASKPASS_SCRIPT}"] if credentialed else []
        return subprocess.run(
            ["git", *cfg, "-C", str(shared_dir), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )

    try:
        run("add", "-A")
        # `git commit` exits 1 (indistinguishable from a real failure by
        # exit code alone) when there's nothing staged, and the message
        # saying so is locale-dependent -- checking for staged changes
        # first avoids parsing that text in any language.
        nothing_staged = run("diff", "--cached", "--quiet").returncode == 0
        if not nothing_staged:
            commit = run("commit", "-m", commit_message)
            if commit.returncode != 0:
                detail = (commit.stderr or commit.stdout).strip()
                logger.warning("git commit failed in %s: %s", shared_dir, detail)
                return f"git commit: {detail}"

        push = run("push", credentialed=True)
        if push.returncode != 0:
            detail = (push.stderr or push.stdout).strip()
            logger.warning("git push failed in %s: %s", shared_dir, detail)
            return f"git push: {detail}"
    except subprocess.TimeoutExpired:
        logger.warning("git push timed out in %s", shared_dir)
        return "git push: превышено время ожидания"
    except OSError as exc:
        logger.warning("git push could not run in %s: %s", shared_dir, exc)
        return f"git push: {exc}"

    return None

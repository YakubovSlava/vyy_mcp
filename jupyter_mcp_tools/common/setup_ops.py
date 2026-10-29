"""Filesystem/git/pip operations shared by bootstrap and the launcher.

Every function here takes a ``log`` callable instead of printing directly.
That's not stylistic: `launcher.py` runs as an MCP stdio transport, where
stdout *is* the JSON-RPC stream, so the quiet path must be able to route or
suppress output entirely. `bootstrap.py` passes ``print``; `launcher.py`
passes a stderr logger.

Nothing here prompts or reads stdin — see `bootstrap.py` for that.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import Callable

from .launcher_config import LauncherConfig

Log = Callable[[str], None]

STAMP_FILE_NAME = ".install-stamp"
LOCK_FILE_NAME = ".launcher.lock"


class SetupError(RuntimeError):
    """A failure whose message is meant to be shown to the user verbatim."""


def _noop(_: str) -> None:
    pass


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------


def git_env(config: LauncherConfig) -> dict[str, str]:
    """Environment for `git`, carrying credentials out-of-band.

    Not `-c http.extraheader=...` (visible to any local `ps`) and not
    credentials inside the remote URL (persisted to `.git/config`, readable
    with `git remote -v`, and left behind after the token is rotated).
    `GIT_CONFIG_*` env vars exist for exactly this and are process-local.
    """
    env = dict(os.environ)
    if config.username and config.token:
        raw = f"{config.username}:{config.token}".encode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = "Authorization: Basic " + base64.b64encode(raw).decode()
    if config.ca_bundle:
        # The same CA bundle the package index uses: a proxy that re-signs
        # HTTPS does the same to git, and GIT_SSL_NO_VERIFY is not a fix.
        env.setdefault("GIT_SSL_CAINFO", config.ca_bundle)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def _git(args: list[str], config: LauncherConfig, *, cwd: Path | None = None) -> None:
    try:
        subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            env=git_env(config),
            check=True,
            # Never stdout: `git` writes progress there on some setups, and
            # this module is also used from the MCP stdio path.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SetupError("git не найден в PATH — установите git и повторите.") from exc
    except subprocess.CalledProcessError as exc:
        lines = (exc.stderr or "").strip().splitlines()
        tail = lines[-1] if lines else f"код {exc.returncode}"
        raise SetupError(
            f"git {' '.join(args)} завершился с ошибкой: {tail}\n"
            "Проверьте адрес репозитория, логин и срок действия токена."
        ) from exc


def clone_or_pull(config: LauncherConfig, *, log: Log = _noop, strict: bool = True) -> Path:
    """Clone into `cache_dir`, or fast-forward the existing checkout.

    `strict=False` (the launcher's mode) makes a failed pull non-fatal: an
    expired token or a brief network outage must leave a working, if
    slightly stale, server rather than no server.
    """
    cache = Path(config.cache_dir).expanduser()
    if (cache / ".git").is_dir():
        try:
            _git(["pull", "--ff-only", "-q"], config, cwd=cache)
        except SetupError as exc:
            if strict:
                raise
            log(f"не удалось обновить навыки ({exc}) — использую текущую копию")
        return cache
    if not strict:
        raise SetupError(f"{cache} — не git-репозиторий. Запустите setup заново.")
    cache.parent.mkdir(parents=True, exist_ok=True)
    log(f"клонирую {config.repo_url} -> {cache}")
    _git(["clone", "-q", config.repo_url, str(cache)], config)
    return cache


# --------------------------------------------------------------------------
# package install
# --------------------------------------------------------------------------


def index_env(config: LauncherConfig) -> dict[str, str]:
    """Environment applying the corporate package index and TLS trust.

    Both `PIP_TRUSTED_HOST` and a CA bundle are supported: the first is
    what people reach for, the second is the correct answer when a proxy
    re-signs packages. Env vars rather than flags so the settings also
    reach subprocesses and `uv`.
    """
    env = dict(os.environ)
    if config.index_url:
        env["PIP_INDEX_URL"] = config.index_url
        env["UV_INDEX_URL"] = config.index_url
    if config.trusted_hosts:
        env["PIP_TRUSTED_HOST"] = " ".join(config.trusted_hosts)
    if config.ca_bundle:
        env["PIP_CERT"] = config.ca_bundle
        env["REQUESTS_CA_BUNDLE"] = config.ca_bundle
        env["SSL_CERT_FILE"] = config.ca_bundle
        env["UV_NATIVE_TLS"] = "false"
    return env


def uv_flags(config: LauncherConfig) -> list[str]:
    """uv's spelling of pip's `--trusted-host`.

    `uv` has no `--trusted-host`; handing it pip's flags is silently
    ignored and the install quietly goes to public PyPI instead.
    """
    flags: list[str] = []
    for host in config.trusted_hosts:
        flags += ["--allow-insecure-host", host]
    return flags


def venv_python(cache: Path) -> Path:
    if os.name == "nt":
        return cache / ".venv" / "Scripts" / "python.exe"
    return cache / ".venv" / "bin" / "python"


def install_stamp(cache: Path, config: LauncherConfig) -> str:
    """Fingerprint of "what should be installed".

    Covers `requirements.txt` (a pushed skill can change it) and the
    index/trust settings. Excludes timestamps on purpose: an unchanged
    fingerprint means "skip pip", which keeps a warm launch to a pull plus
    an `exec`. Without this, every agent start pays a 5-15s network round
    trip.
    """
    requirements = cache / "requirements.txt"
    payload = requirements.read_text(encoding="utf-8") if requirements.exists() else ""
    payload += f"|{config.index_url}|{','.join(config.trusted_hosts)}|{config.ca_bundle}"
    return hashlib.sha256(payload.encode()).hexdigest()


def needs_install(cache: Path, config: LauncherConfig) -> bool:
    stamp = cache / STAMP_FILE_NAME
    if not stamp.exists():
        return True
    return stamp.read_text(encoding="utf-8").strip() != install_stamp(cache, config)


def write_stamp(cache: Path, config: LauncherConfig) -> None:
    (cache / STAMP_FILE_NAME).write_text(install_stamp(cache, config) + "\n", encoding="utf-8")


def install(cache: Path, config: LauncherConfig, *, log: Log = _noop) -> Path:
    """Create the venv if missing, then install the project editable."""
    python = venv_python(cache)
    if not python.exists():
        log(f"создаю venv в {cache / '.venv'}")
        venv.EnvBuilder(with_pip=True, clear=False).create(str(cache / ".venv"))
        python = venv_python(cache)

    log("устанавливаю зависимости")

    uv = shutil.which("uv")
    if uv:
        # `--python` is required: `uv pip install` otherwise targets the
        # *active* environment and fails with "No virtual environment
        # found" — the venv just created here isn't activated.  Found on a
        # real end-to-end run, not in unit tests (which stub out install).
        cmd = [uv, "pip", "install", *uv_flags(config), "--python", str(python), "-e", str(cache)]
    else:
        cmd = [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-e",
            str(cache),
        ]

    try:
        subprocess.run(cmd, env=index_env(config), check=True, stdout=sys.stderr, stderr=sys.stderr)
    except subprocess.CalledProcessError as exc:
        raise SetupError(
            f"установка зависимостей не удалась (код {exc.returncode}).\n"
            "Если пакеты берутся из корпоративного индекса, проверьте адрес "
            "индекса, trusted hosts и CA-сертификат."
        ) from exc

    write_stamp(cache, config)
    return python


# --------------------------------------------------------------------------
# launcher state
# --------------------------------------------------------------------------


def dump_json(payload: object) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)

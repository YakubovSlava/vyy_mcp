"""Non-interactive launcher: the process an MCP client spawns on every start.

One job — get to `jupyter-mcp-tools-server --transport stdio` and hand over
the process — with as few failure modes as possible:

- **Nothing may be written to stdout.** stdout is the MCP JSON-RPC stream;
  a stray `print`, a `git` progress line or a pip banner breaks the
  protocol, and the client reports a server that "failed to start". All
  diagnostics go to stderr, which clients surface separately.
- **`git pull` is best-effort.** An expired token or a dead network must
  not take the server down; the existing checkout keeps serving.
- **Concurrent pulls are serialized** with an exclusive lock on the cache
  dir. Agents routinely start several sessions at once, and two pulls in
  one checkout produce `cannot lock ref` / `index.lock` — i.e. a dead
  server in one of them.
- **Installs happen on a change of stamp, not on every start**, so a warm
  launch is a pull plus an `exec`.

The lock uses `fcntl` on POSIX and `msvcrt` on Windows rather than a
lockfile-with-pid: a crashed previous session must never wedge the next one.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from .common import setup_ops
from .common.launcher_config import LauncherConfig, load_launcher_config

MISSING_SETUP_MESSAGE = (
    "jupyter-mcp-tools: локальная копия ещё не настроена.\n"
    "Выполните один раз в терминале:\n\n"
    "    jupyter-mcp-tools-setup\n\n"
    "(или запустите scripts/bootstrap.py из клона репозитория)"
)


def _log(message: str) -> None:
    # stderr, always — see the module docstring.
    print(f"jupyter-mcp-tools: {message}", file=sys.stderr)


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            # Blocking on purpose: the point is to wait for the other pull
            # rather than race it. msvcrt has no timeout variant.
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _find_uvx() -> str | None:
    found = shutil.which("uvx")
    if found:
        return found
    for candidate in (
        Path.home() / ".local" / "bin" / "uvx",
        Path.home() / ".local" / "bin" / "uvx.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def server_argv(cache: Path, config: LauncherConfig, extra: list[str]) -> list[str]:
    """Command line for the server itself: venv first, `uvx` as fallback."""
    shared = cache / "shared_tools"
    python = Path(config.python) if config.python else setup_ops.venv_python(cache)
    if python.exists():
        return [
            str(python),
            "-m",
            "jupyter_mcp_tools.mcp_server.server",
            "--transport",
            "stdio",
            "--shared-dir",
            str(shared),
            *extra,
        ]
    # No venv (setup --skip-install, or the user deleted it): fall back to
    # uvx, the path the older run_from_git.sh took. Still serves skills from
    # this checkout's shared_tools/.
    uvx = _find_uvx()
    if not uvx:
        raise SystemExit(
            "jupyter-mcp-tools: не найден ни venv, ни uvx.\n"
            "Запустите jupyter-mcp-tools-setup заново, чтобы создать окружение."
        )
    return [
        uvx,
        "--from",
        str(cache),
        "jupyter-mcp-tools-server",
        "--transport",
        "stdio",
        "--shared-dir",
        str(shared),
        *extra,
    ]


def prepare(cache: Path, config: LauncherConfig) -> None:
    """Pull, then install only if the fingerprint moved. Never raises."""
    with _exclusive_lock(cache / setup_ops.LOCK_FILE_NAME):
        try:
            setup_ops.clone_or_pull(config, log=_log, strict=False)
        except setup_ops.SetupError as exc:
            _log(f"{exc} — использую текущую копию")
            return
        if config.python and setup_ops.needs_install(cache, config):
            try:
                setup_ops.install(cache, config, log=_log)
            except Exception as exc:  # noqa: BLE001 - a stale env still works
                _log(f"обновление зависимостей не удалось: {exc} — "
                     "использую текущее окружение")


def main(argv: list[str] | None = None) -> None:
    extra = list(sys.argv[1:] if argv is None else argv)
    config = load_launcher_config()
    if not config or not config.cache_dir:
        raise SystemExit(MISSING_SETUP_MESSAGE)

    cache = Path(config.cache_dir).expanduser()
    if not (cache / ".git").is_dir():
        raise SystemExit(
            f"jupyter-mcp-tools: {cache} — не git-репозиторий.\n"
            "Запустите jupyter-mcp-tools-setup заново."
        )

    prepare(cache, config)
    argv_out = server_argv(cache, config, extra)

    if os.name == "nt":
        # No exec on Windows: spawn and propagate the exit code.
        raise SystemExit(subprocess.run(argv_out).returncode)
    # exec: the server replaces this process, so nothing sits between the
    # MCP client and the server (no orphan left behind on client shutdown,
    # no signal forwarding to hand-roll).
    os.execv(argv_out[0], argv_out)


if __name__ == "__main__":  # pragma: no cover
    main()

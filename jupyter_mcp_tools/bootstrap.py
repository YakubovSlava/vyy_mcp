"""Interactive first-time setup for an end user's local MCP server install.

The "log in with your repo credentials" step a user runs *once*, in a
terminal, themselves. It replaces the assumption baked into the older
`scripts/run_from_git.sh` that the repo is public and PyPI is reachable:

1. Asks for the repo URL, a username + token for it, and the corporate
   package index (plus trusted hosts / a CA bundle if the org needs them).
2. Clones the repo, or fast-forwards the existing checkout.
3. Creates a venv in that checkout and installs the project from the
   configured index.
4. Saves everything to `~/.jupyter_mcp_tools/launcher.json` (`0600` — it
   holds a token) and prints the snippet to paste into an MCP client.

Split from `jupyter_mcp_tools.launcher` (what an agent spawns on every
start) on purpose: **this** module is interactive and prints; the launcher
must never write to stdout, because there stdout is the JSON-RPC stream.

Imports nothing outside the standard library, so `scripts/bootstrap.py` can
run it from a bare `python3` before the project (or any dependency) is
installed anywhere.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from .common import setup_ops
from .common.launcher_config import (
    LauncherConfig,
    launcher_config_path,
    load_launcher_config,
    save_launcher_config,
)

DEFAULT_REPO_URL = "https://github.com/YakubovSlava/vyy_mcp.git"
DEFAULT_CACHE_DIR = "~/.cache/jupyter-mcp-tools-src"


def default_cache_dir() -> Path:
    return Path(DEFAULT_CACHE_DIR).expanduser()


def _print(message: str) -> None:
    print(f"jupyter-mcp-tools: {message}")


# --------------------------------------------------------------------------
# prompting
# --------------------------------------------------------------------------


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def _ask_list(prompt: str, default: list[str]) -> list[str]:
    raw = _ask(prompt, " ".join(default))
    return [part for part in raw.replace(",", " ").split() if part]


def prompt_config(base: LauncherConfig) -> LauncherConfig:
    print()
    print("Настройка локальной копии MCP-сервера.")
    print(f"Токен спрашивается без эха и сохраняется только в {launcher_config_path()}")
    print("(права 0600) — не в конфиг агента, который легко утекает в git.")
    print()

    repo_url = _ask("URL git-репозитория", base.repo_url or DEFAULT_REPO_URL)
    username = _ask("Логин для репозитория (пусто — репозиторий публичный)", base.username)
    token = ""
    if username:
        token = getpass.getpass("Токен (ввод не отображается): ").strip()
        if not token and base.token:
            token = base.token
    cache_dir = _ask("Каталог для клона", base.cache_dir or str(default_cache_dir()))
    index_url = _ask("URL индекса пакетов, замена PyPI (пусто — публичный PyPI)", base.index_url)
    trusted_hosts = _ask_list(
        "Trusted hosts через пробел (например index.corp.local)", base.trusted_hosts
    )
    ca_bundle = _ask(
        "Путь к CA-сертификату (если HTTPS подменяет прокси; иначе пусто)", base.ca_bundle
    )

    return LauncherConfig(
        repo_url=repo_url,
        cache_dir=cache_dir,
        python="",
        index_url=index_url,
        trusted_hosts=trusted_hosts,
        ca_bundle=ca_bundle,
        username=username,
        token=token,
    )


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------


def launcher_path(checkout: Path) -> Path:
    name = "run_local.bat" if os.name == "nt" else "run_local.sh"
    return checkout / "scripts" / name


def mcp_snippet(checkout: Path) -> str:
    """Client config to paste — points at the launcher, never at the venv."""
    runner = str(launcher_path(checkout))
    if os.name == "nt":
        command, args = "cmd", ["/c", runner]
    else:
        command, args = "sh", [runner]
    return setup_ops.dump_json(
        {"mcpServers": {"jupyter-mcp-tools": {"command": command, "args": args}}}
    )


def print_next_steps(checkout: Path) -> None:
    print()
    print("Готово. Вставьте в конфиг MCP-клиента:")
    print()
    print(mcp_snippet(checkout))
    print()
    print(f"Навыки: {checkout / 'shared_tools'}")
    print("Перезапустите агента. При каждом старте лаунчер делает git pull,")
    print("поэтому навыки, добавленные в админке, подхватываются сами.")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def build_config_from_args(args: argparse.Namespace, current: LauncherConfig | None) -> LauncherConfig:
    base = current or LauncherConfig(
        repo_url=DEFAULT_REPO_URL, cache_dir=str(default_cache_dir())
    )
    if args.repo_url:
        base.repo_url = args.repo_url
    if args.cache_dir:
        base.cache_dir = args.cache_dir
    if args.username:
        base.username = args.username
    if args.index_url:
        base.index_url = args.index_url
    if args.trusted_host:
        base.trusted_hosts = list(args.trusted_host)
    if args.ca_bundle:
        base.ca_bundle = args.ca_bundle
    if args.token_env:
        token = os.environ.get(args.token_env, "")
        if token:
            base.token = token
    return base


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jupyter-mcp-tools-setup",
        description=(
            "Одноразовая настройка локальной копии MCP-сервера: логин/токен "
            "репозитория, корпоративный индекс пакетов, venv."
        ),
    )
    parser.add_argument("--repo-url", help="URL git-репозитория")
    parser.add_argument("--username", help="логин для репозитория")
    parser.add_argument(
        "--token-env",
        metavar="VAR",
        help="взять токен из этой переменной окружения, не спрашивать",
    )
    parser.add_argument("--cache-dir", help="каталог для клона")
    parser.add_argument("--index-url", help="URL индекса пакетов (замена PyPI)")
    parser.add_argument(
        "--trusted-host",
        action="append",
        default=None,
        metavar="HOST",
        help="host без проверки TLS для pip (можно указать несколько раз)",
    )
    parser.add_argument("--ca-bundle", help="путь к CA-сертификату")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="не задавать вопросов; берёт всё из аргументов и --token-env",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="только клонировать и записать конфиг, venv не создавать",
    )
    args = parser.parse_args(argv)

    try:
        current = load_launcher_config()
    except Exception:  # noqa: BLE001 - a corrupt file just means "start over"
        current = None

    config = build_config_from_args(args, current)
    if args.non_interactive:
        # Defaults exist, so "quietly use them" would mean a CI/scripted run
        # cloning into ~/.cache and creating a venv there by accident. In
        # non-interactive mode both must be stated explicitly.
        missing = [
            flag
            for flag, value in (("--repo-url", args.repo_url), ("--cache-dir", args.cache_dir))
            if not value
        ]
        if missing:
            print(
                f"jupyter-mcp-tools: для --non-interactive укажите {', '.join(missing)}.",
                file=sys.stderr,
            )
            return 2
    else:
        config = prompt_config(config)

    try:
        checkout = setup_ops.clone_or_pull(config, log=_print)
        if args.skip_install:
            _print(f"клон готов: {checkout} (установка пропущена)")
            config.python = ""
        else:
            config.python = str(setup_ops.install(checkout, config, log=_print))
    except setup_ops.SetupError as exc:
        print(f"jupyter-mcp-tools: {exc}", file=sys.stderr)
        return 1

    saved = save_launcher_config(config)
    _print(f"конфигурация сохранена: {saved}")
    print_next_steps(checkout)
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    main()

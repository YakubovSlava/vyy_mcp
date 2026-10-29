"""Tests for the two entry points an end user actually touches.

The launcher matters most: it runs as an MCP stdio transport, so a single
byte on stdout is a protocol violation that surfaces to the user as "the
server failed to start" — with no hint that a stray print caused it. That
gets its own regression test below.
"""
import os
import sys

import pytest

from jupyter_mcp_tools import bootstrap, launcher
from jupyter_mcp_tools.common.launcher_config import LauncherConfig, save_launcher_config


@pytest.fixture
def isolated_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_TOOLS_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


# --------------------------------------------------------------------------
# bootstrap: prompting
# --------------------------------------------------------------------------


def _answers(monkeypatch, values):
    """Feed `input()`/`getpass()` a scripted list of answers."""
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(it))
    monkeypatch.setattr(bootstrap.getpass, "getpass", lambda _prompt="": next(it))


def test_prompt_collects_credentials_and_index(monkeypatch):
    _answers(
        monkeypatch,
        [
            "https://git.corp/team/repo.git",  # repo url
            "alice",  # username
            "t0ken",  # token
            "/tmp/clone",  # cache dir
            "https://index.corp/simple",  # index url
            "index.corp files.corp",  # trusted hosts
            "/etc/ssl/corp.pem",  # ca bundle
        ],
    )
    cfg = bootstrap.prompt_config(LauncherConfig())
    assert cfg.repo_url == "https://git.corp/team/repo.git"
    assert cfg.username == "alice"
    assert cfg.token == "t0ken"
    assert cfg.cache_dir == "/tmp/clone"
    assert cfg.index_url == "https://index.corp/simple"
    assert cfg.trusted_hosts == ["index.corp", "files.corp"]
    assert cfg.ca_bundle == "/etc/ssl/corp.pem"


def test_prompt_accepts_comma_separated_trusted_hosts(monkeypatch):
    _answers(
        monkeypatch,
        [
            "",  # repo url -> default
            "",  # username -> none
            "",  # cache dir -> default
            "",  # index url
            "index.corp, files.corp",  # trusted hosts
            "",  # ca bundle
        ],
    )
    cfg = bootstrap.prompt_config(LauncherConfig(repo_url="r", cache_dir="/c"))
    assert cfg.trusted_hosts == ["index.corp", "files.corp"]


def test_prompt_keeps_previous_token_when_left_blank(monkeypatch):
    _answers(monkeypatch, ["", "alice", "", "", "", "", ""])
    cfg = bootstrap.prompt_config(
        LauncherConfig(repo_url="r", cache_dir="/c", username="alice", token="old")
    )
    # Re-running setup to change only the index must not wipe the token.
    assert cfg.token == "old"


def test_public_repo_asks_no_token(monkeypatch):
    _answers(monkeypatch, ["https://git/rep.git", "", "/tmp/c", "", "", ""])
    cfg = bootstrap.prompt_config(LauncherConfig())
    assert cfg.username == ""
    assert cfg.token == ""


# --------------------------------------------------------------------------
# bootstrap: non-interactive
# --------------------------------------------------------------------------


def test_non_interactive_requires_repo_and_cache(isolated_data_dir, capsys):
    rc = bootstrap.run(["--non-interactive"])
    assert rc == 2
    assert "--repo-url" in capsys.readouterr().err


def test_non_interactive_does_not_touch_the_default_cache(isolated_data_dir, tmp_path, monkeypatch, capsys):
    """Regression: a scripted run must not clone into ~/.cache by default.

    Found while writing these tests: defaults were silently filled in for
    `--non-interactive`, so a bare `--non-interactive` invocation created a
    venv under the user's home instead of failing fast.
    """
    monkeypatch.setattr(bootstrap, "default_cache_dir", lambda: tmp_path / "default-cache")
    rc = bootstrap.run(["--non-interactive"])
    assert rc == 2
    assert not (tmp_path / "default-cache").exists()
    assert not (isolated_data_dir / "launcher.json").exists()


def test_token_env_keeps_token_out_of_argv(monkeypatch, isolated_data_dir, tmp_path):
    monkeypatch.setenv("REPO_TOKEN", "from-env")
    args = bootstrap.argparse.Namespace(
        repo_url="https://git/r.git",
        username="alice",
        token_env="REPO_TOKEN",
        cache_dir=str(tmp_path / "c"),
        index_url="",
        trusted_host=[],
        ca_bundle="",
    )
    cfg = bootstrap.build_config_from_args(args, None)
    assert cfg.token == "from-env"


# --------------------------------------------------------------------------
# the snippet points at the launcher, and is pasteable as-is
# --------------------------------------------------------------------------


def test_snippet_targets_the_launcher_not_the_venv(tmp_path):
    import json

    payload = json.loads(bootstrap.mcp_snippet(tmp_path))
    entry = payload["mcpServers"]["jupyter-mcp-tools"]
    joined = " ".join([entry["command"], *entry["args"]])
    assert "run_local" in joined
    # Pointing the client at the venv python directly would skip the pull
    # and the lock, which are the whole point.
    assert ".venv" not in joined


def test_snippet_uses_cmd_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap.os, "name", "nt")
    import json

    entry = json.loads(bootstrap.mcp_snippet(tmp_path))["mcpServers"]["jupyter-mcp-tools"]
    assert entry["command"] == "cmd"
    assert entry["args"][0] == "/c"
    assert entry["args"][1].endswith("run_local.bat")


# --------------------------------------------------------------------------
# launcher
# --------------------------------------------------------------------------


def test_launcher_explains_missing_setup(isolated_data_dir):
    with pytest.raises(SystemExit) as exc:
        launcher.main([])
    assert "jupyter-mcp-tools-setup" in str(exc.value)


def test_launcher_rejects_cache_that_is_not_a_checkout(isolated_data_dir, tmp_path):
    save_launcher_config(LauncherConfig(repo_url="r", cache_dir=str(tmp_path / "plain")))
    (tmp_path / "plain").mkdir()
    with pytest.raises(SystemExit) as exc:
        launcher.main([])
    assert "не git-репозиторий" in str(exc.value)


def test_server_argv_prefers_the_venv(tmp_path):
    cache = tmp_path / "clone"
    (cache / "shared_tools").mkdir(parents=True)
    venv_bin = cache / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    venv_bin.mkdir(parents=True)
    python = venv_bin / ("python.exe" if os.name == "nt" else "python")
    python.write_text("")

    argv = launcher.server_argv(cache, LauncherConfig(cache_dir=str(cache)), [])
    assert argv[0] == str(python)
    assert "--shared-dir" in argv
    # Serving from the checkout's shared_tools is what makes the pull an
    # auto-update of the skill set.
    assert argv[argv.index("--shared-dir") + 1] == str(cache / "shared_tools")


def test_server_argv_forwards_extra_flags(tmp_path):
    cache = tmp_path / "clone"
    (cache / "shared_tools").mkdir(parents=True)
    venv_bin = cache / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    venv_bin.mkdir(parents=True)
    (venv_bin / ("python.exe" if os.name == "nt" else "python")).write_text("")

    argv = launcher.server_argv(cache, LauncherConfig(cache_dir=str(cache)), ["--log-level", "DEBUG"])
    assert argv[-2:] == ["--log-level", "DEBUG"]


def test_prepare_never_raises_on_a_broken_pull(tmp_path):
    """A dead remote must not stop the server from starting."""
    cache = tmp_path / "clone"
    cache.mkdir()
    (cache / ".git").mkdir()  # looks like a checkout, has no real remote

    cfg = LauncherConfig(repo_url=str(tmp_path / "gone.git"), cache_dir=str(cache))
    launcher.prepare(cache, cfg)  # must not raise

    # And `git pull`'s chatter must not reach stdout (see the protocol test).
    assert (cache / ".launcher.lock").exists()


def test_stdout_stays_clean_on_a_failed_pull(tmp_path, capsys):
    """Regression: stdout of the launcher IS the MCP JSON-RPC stream.

    Any diagnostic on stdout makes the client report "server failed to
    start" with no useful error, so this pins the contract explicitly.
    """
    cache = tmp_path / "clone"
    (cache / ".git").mkdir(parents=True)

    cfg = LauncherConfig(repo_url=str(tmp_path / "gone.git"), cache_dir=str(cache))
    launcher.prepare(cache, cfg)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "копия" in captured.err or "навыки" in captured.err

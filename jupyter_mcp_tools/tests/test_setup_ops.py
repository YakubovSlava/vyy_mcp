import base64
import subprocess

import pytest

from jupyter_mcp_tools.common import setup_ops
from jupyter_mcp_tools.common.launcher_config import (
    LauncherConfig,
    launcher_config_path,
    load_launcher_config,
    save_launcher_config,
)


@pytest.fixture
def cfg(tmp_path):
    return LauncherConfig(
        repo_url="https://git.example.org/team/vyy_mcp.git",
        cache_dir=str(tmp_path / "clone"),
        username="alice",
        token="s3cr3t",
    )


# --------------------------------------------------------------------------
# launcher.json: the file that holds the token
# --------------------------------------------------------------------------


def test_launcher_config_defaults_to_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_TOOLS_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert launcher_config_path() == tmp_path / ".jupyter_mcp_tools" / "launcher.json"


def test_launcher_config_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_TOOLS_DATA_DIR", str(tmp_path / "data"))
    saved = LauncherConfig(
        repo_url="https://git.example.org/r.git",
        cache_dir="/tmp/c",
        python="/tmp/c/.venv/bin/python",
        index_url="https://index.corp.local/simple",
        trusted_hosts=["index.corp.local", "files.corp.local"],
        ca_bundle="/etc/ssl/corp.pem",
        username="alice",
        token="tok",
    )
    save_launcher_config(saved)
    assert load_launcher_config() == saved


def test_launcher_config_is_0600_because_it_holds_a_token(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_TOOLS_DATA_DIR", str(tmp_path / "data"))
    path = save_launcher_config(LauncherConfig(username="a", token="t"))
    # Same reasoning as config.json: a plaintext credential must not be
    # world-readable on a shared filesystem.
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_load_launcher_config_returns_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_TOOLS_DATA_DIR", str(tmp_path / "nothing"))
    assert load_launcher_config() is None


# --------------------------------------------------------------------------
# git: credentials out-of-band
# --------------------------------------------------------------------------


def test_git_env_passes_token_as_header_not_url(cfg):
    env = setup_ops.git_env(cfg)
    expected = "Authorization: Basic " + base64.b64encode(b"alice:s3cr3t").decode()
    assert env["GIT_CONFIG_KEY_0"] == "http.extraheader"
    assert env["GIT_CONFIG_VALUE_0"] == expected
    # The URL itself must stay clean: credentials embedded there land in
    # .git/config and show up in `git remote -v`.
    assert "s3cr3t" not in cfg.repo_url
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_git_env_omits_header_without_credentials(tmp_path):
    env = setup_ops.git_env(LauncherConfig(repo_url="x", cache_dir=str(tmp_path)))
    assert "GIT_CONFIG_KEY_0" not in env


def test_git_env_uses_ca_bundle_for_a_resigning_proxy(cfg):
    cfg.ca_bundle = "/etc/ssl/corp.pem"
    assert setup_ops.git_env(cfg)["GIT_SSL_CAINFO"] == "/etc/ssl/corp.pem"


# --------------------------------------------------------------------------
# package index
# --------------------------------------------------------------------------


def test_index_env_maps_index_and_trust(cfg):
    cfg.index_url = "https://index.corp.local/simple"
    cfg.trusted_hosts = ["index.corp.local"]
    cfg.ca_bundle = "/etc/ssl/corp.pem"
    env = setup_ops.index_env(cfg)
    assert env["PIP_INDEX_URL"] == "https://index.corp.local/simple"
    assert env["UV_INDEX_URL"] == "https://index.corp.local/simple"
    assert env["PIP_TRUSTED_HOST"] == "index.corp.local"
    assert env["PIP_CERT"] == "/etc/ssl/corp.pem"
    assert env["REQUESTS_CA_BUNDLE"] == "/etc/ssl/corp.pem"
    assert env["SSL_CERT_FILE"] == "/etc/ssl/corp.pem"


def test_uv_uses_its_own_trust_flag(cfg):
    cfg.trusted_hosts = ["index.corp.local"]
    # Regression guard: uv has no --trusted-host. Handing it pip's spelling
    # is silently ignored, so the install goes to public PyPI instead.
    assert setup_ops.uv_flags(cfg) == ["--allow-insecure-host", "index.corp.local"]


def test_index_env_leaves_pypi_alone_by_default(cfg):
    env = setup_ops.index_env(cfg)
    assert env.get("PIP_INDEX_URL") == __import__("os").environ.get("PIP_INDEX_URL")
    assert "PIP_TRUSTED_HOST" not in env


# --------------------------------------------------------------------------
# stamp: install once per change, not once per launch
# --------------------------------------------------------------------------


def test_stamp_missing_on_fresh_checkout(tmp_path, cfg):
    cache = tmp_path / "clone"
    cache.mkdir()
    assert setup_ops.needs_install(cache, cfg)


def test_stamp_skips_reinstall_when_nothing_changed(tmp_path, cfg):
    cache = tmp_path / "clone"
    cache.mkdir()
    (cache / "requirements.txt").write_text("mcp>=1.6\n")
    setup_ops.write_stamp(cache, cfg)
    assert not setup_ops.needs_install(cache, cfg)


def test_stamp_changes_when_requirements_change(tmp_path, cfg):
    cache = tmp_path / "clone"
    cache.mkdir()
    (cache / "requirements.txt").write_text("mcp>=1.6\n")
    setup_ops.write_stamp(cache, cfg)
    # A pushed skill can bump requirements.txt -- that must trigger a real
    # install rather than being skipped forever.
    (cache / "requirements.txt").write_text("mcp>=1.6\nrequests>=2.31\n")
    assert setup_ops.needs_install(cache, cfg)


def test_stamp_changes_when_index_changes(tmp_path, cfg):
    cache = tmp_path / "clone"
    cache.mkdir()
    setup_ops.write_stamp(cache, cfg)
    cfg.index_url = "https://index.corp.local/simple"
    assert setup_ops.needs_install(cache, cfg)


# --------------------------------------------------------------------------
# clone / pull against a real local repo
# --------------------------------------------------------------------------


def _make_remote(tmp_path):
    bare = tmp_path / "remote.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "-q"], cwd=bare, check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "T")):
        subprocess.run(["git", "config", key, value], cwd=seed, check=True)
    (seed / "requirements.txt").write_text("mcp>=1.6\n")
    (seed / "shared_tools").mkdir()
    (seed / "shared_tools" / "README.md").write_text("skills\n")
    subprocess.run(["git", "add", "-A"], cwd=seed, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=seed, check=True)
    subprocess.run(["git", "push", "-q", "origin", "HEAD"], cwd=seed, check=True)
    return bare, seed


def test_clone_then_pull_picks_up_new_skills(tmp_path):
    bare, seed = _make_remote(tmp_path)
    cfg = LauncherConfig(repo_url=str(bare), cache_dir=str(tmp_path / "clone"))

    cache = setup_ops.clone_or_pull(cfg)
    assert (cache / "shared_tools" / "README.md").exists()

    # The whole point of pulling on every launch: a skill added upstream
    # (by the admin UI pushing into this repo) shows up locally.
    (seed / "shared_tools" / "new_skill").mkdir()
    (seed / "shared_tools" / "new_skill" / "SKILL.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=seed, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add skill"], cwd=seed, check=True)
    subprocess.run(["git", "push", "-q", "origin", "HEAD"], cwd=seed, check=True)

    setup_ops.clone_or_pull(cfg)
    assert (cache / "shared_tools" / "new_skill" / "SKILL.md").exists()


def test_failed_pull_is_tolerated_when_not_strict(tmp_path):
    bare, _seed = _make_remote(tmp_path)
    cfg = LauncherConfig(repo_url=str(bare), cache_dir=str(tmp_path / "clone"))
    cache = setup_ops.clone_or_pull(cfg)

    # A dead remote (expired token / no network) must not take the server
    # down; the checkout keeps serving what it has.
    cfg.repo_url = str(tmp_path / "gone.git")
    subprocess.run(["git", "remote", "set-url", "origin", cfg.repo_url], cwd=cache, check=True)
    assert setup_ops.clone_or_pull(cfg, strict=False) == cache
    with pytest.raises(setup_ops.SetupError):
        setup_ops.clone_or_pull(cfg, strict=True)


def test_missing_git_reports_actionable_error(tmp_path, monkeypatch, cfg):
    cache = tmp_path / "not_a_repo"
    cache.mkdir()
    cfg.cache_dir = str(cache)
    with pytest.raises(setup_ops.SetupError, match="не git-репозиторий"):
        setup_ops.clone_or_pull(cfg, strict=False)


# --------------------------------------------------------------------------
# packaging details that only fail on a real clone
# --------------------------------------------------------------------------


def test_launcher_scripts_are_executable_in_the_repo():
    """Regression: found live, not in tests.

    `scripts/run_local.sh` landed in git as mode 100644, so a fresh clone
    got a non-executable script — and an MCP client pointed at it fails
    with "Permission denied" and no server. The mode has to be right in the
    index, not just on the developer's disk.
    """
    import os
    import subprocess

    root = setup_ops.Path(__file__).resolve().parents[2]
    for name in ("scripts/run_local.sh", "scripts/bootstrap.py"):
        mode = subprocess.run(
            ["git", "ls-files", "-s", name],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()[0]
        assert mode == "100755", f"{name} is committed as {mode}, needs 100755"


def test_local_launcher_state_is_gitignored():
    """The venv, stamp and lock live inside the checkout and must never be
    pushed back to the shared repo (the admin UI pushes into it)."""
    root = setup_ops.Path(__file__).resolve().parents[2]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    for entry in (".install-stamp", ".launcher.lock", ".venv/"):
        assert entry in ignored

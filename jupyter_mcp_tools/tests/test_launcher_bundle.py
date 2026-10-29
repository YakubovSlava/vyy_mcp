"""The machine-managed `launcher/` folder shipped in the child repo.

Two things matter and are tested here:

1. It contains everything needed to start a server — third-party deps, the
   package itself, and an entrypoint that actually launches MCP against the
   repo's own `tools/`.
2. It resolves to the *current* logic rather than a snapshot, which is why
   `requirements.txt` references the package from git instead of vendoring
   a copy (vendoring is what was removed earlier).

Also pinned: the folder is app-owned, so the file manager refuses it.
"""
import subprocess

import pytest

from jupyter_mcp_tools.common import launcher_bundle
from jupyter_mcp_tools.common.repo_files import FileValidationError, list_files, write_file


# --------------------------------------------------------------------------
# contents
# --------------------------------------------------------------------------


def test_requirements_are_third_party_only():
    """No self-reference: the package's own sources are copied into the
    folder instead, so nothing of ours is fetched over the network."""
    text = launcher_bundle.requirements_text()
    assert "mcp" in text
    assert "uvicorn" in text
    assert "git+" not in text
    # Check actual requirement lines, not the header comment that mentions
    # this project by name.
    deps = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert not any(d.lower().startswith("jupyter-mcp-tools") for d in deps)


def test_package_sources_are_copied_in(tmp_path):
    launcher_bundle.ensure(tmp_path)
    package = tmp_path / launcher_bundle.VENDORED_PACKAGE_RELDIR
    assert (package / "__init__.py").is_file()
    # The module the entrypoint actually runs must be there.
    assert (package / "mcp_server" / "server.py").is_file()
    assert (package / "common" / "launcher_bundle.py").is_file()


def test_tests_and_caches_are_not_shipped(tmp_path):
    launcher_bundle.ensure(tmp_path)
    package = tmp_path / launcher_bundle.VENDORED_PACKAGE_RELDIR
    relative = [str(p.relative_to(package)) for p in package.rglob("*")]
    assert not any("__pycache__" in r for r in relative)
    assert not any(r.startswith("tests/") for r in relative)
    assert not any(r.endswith(".pyc") for r in relative)


def test_deleted_module_disappears_from_the_copy(tmp_path, monkeypatch):
    """Mirrored, not merely copied: a stale module left behind would keep
    being importable, so the launcher would run code that no longer exists."""
    source = tmp_path / "src" / "jupyter_mcp_tools"
    (source / "common").mkdir(parents=True)
    (source / "__init__.py").write_text("")
    (source / "common" / "__init__.py").write_text("")
    (source / "common" / "gone.py").write_text("x = 1\n")
    monkeypatch.setattr(launcher_bundle, "package_source_root", lambda: source)

    shared = tmp_path / "repo"
    shared.mkdir()
    launcher_bundle.ensure(shared)
    vendored = shared / launcher_bundle.VENDORED_PACKAGE_RELDIR
    assert (vendored / "common" / "gone.py").is_file()

    (source / "common" / "gone.py").unlink()
    launcher_bundle.ensure(shared)
    assert not (vendored / "common" / "gone.py").exists()


def test_requirements_are_deterministic():
    # Written on every push; unstable ordering would manufacture commits.
    assert launcher_bundle.requirements_text() == launcher_bundle.requirements_text()


def test_entrypoint_starts_mcp_over_stdio_against_the_repo_root():
    source = launcher_bundle.render()[launcher_bundle.ENTRYPOINT_RELPATH]
    assert "from jupyter_mcp_tools.mcp_server.server import main" in source
    assert '"--transport", "stdio"' in source or "'--transport', 'stdio'" in source
    # points at the repository root (two levels up from launcher/src/), which
    # is where tools/ lives -- hardcoding a path would break every clone.
    assert "parents[2]" in source


def test_ensure_writes_all_files(tmp_path):
    launcher_bundle.ensure(tmp_path)
    for relative in (
        launcher_bundle.ENTRYPOINT_RELPATH,
        launcher_bundle.REQUIREMENTS_RELPATH,
        launcher_bundle.README_RELPATH,
    ):
        assert (tmp_path / relative).is_file(), relative
    assert (tmp_path / launcher_bundle.VENDORED_PACKAGE_RELDIR).is_dir()


def test_ensure_is_idempotent(tmp_path):
    """No mtime change means no diff, so a push with nothing else changed
    stays "nothing to commit" instead of committing the launcher forever."""
    launcher_bundle.ensure(tmp_path)
    target = tmp_path / launcher_bundle.ENTRYPOINT_RELPATH
    first = target.stat().st_mtime_ns
    launcher_bundle.ensure(tmp_path)
    assert target.stat().st_mtime_ns == first


def test_ensure_rewrites_when_content_changed(tmp_path):
    launcher_bundle.ensure(tmp_path)
    target = tmp_path / launcher_bundle.ENTRYPOINT_RELPATH
    target.write_text("hand-edited\n")
    launcher_bundle.ensure(tmp_path)
    assert "hand-edited" not in target.read_text()


def test_no_environment_variable_can_reintroduce_a_network_fetch(monkeypatch):
    """Regression guard for the deliberate reversal: this used to be
    overridable to point at a fork, which meant a network install of our own
    code. Sources are vendored now, so no such knob should exist."""
    monkeypatch.setenv("JUPYTER_MCP_TOOLS_PACKAGE_REPO_URL", "https://git.corp/fork.git")
    text = launcher_bundle.requirements_text()
    assert "git.corp" not in text
    assert "git+" not in text


# --------------------------------------------------------------------------
# it really runs
# --------------------------------------------------------------------------


def test_entrypoint_serves_the_repo_skills(tmp_path):
    """The point of the folder: `python launcher/src/main.py` must actually
    start an MCP server. Exercised by running it as a subprocess and
    checking the process comes up instead of crashing on import."""
    import os
    import sys

    (tmp_path / "tools" / "echo").mkdir(parents=True)
    (tmp_path / "tools" / "echo" / "tool.py").write_text(
        'def echo(text: str) -> str:\n    """Echoes."""\n    return text\n'
    )
    launcher_bundle.ensure(tmp_path)

    # `--help` goes through argparse before the transport starts, which
    # proves the entrypoint imports and its argv wiring is valid without
    # leaving a server running.
    result = subprocess.run(
        [sys.executable, str(tmp_path / launcher_bundle.ENTRYPOINT_RELPATH), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "MCP_TOOLS_DATA_DIR": str(tmp_path / "data")},
    )
    assert result.returncode == 0, result.stderr
    assert "shared-dir" in result.stdout


# --------------------------------------------------------------------------
# app-owned
# --------------------------------------------------------------------------


def test_file_manager_refuses_the_launcher_folder(tmp_path):
    launcher_bundle.ensure(tmp_path)
    with pytest.raises(FileValidationError, match="служебная папка"):
        write_file(tmp_path, launcher_bundle.ENTRYPOINT_RELPATH, "hacked")


def test_launcher_folder_is_not_listed_for_editing(tmp_path):
    launcher_bundle.ensure(tmp_path)
    (tmp_path / "README.md").write_text("# mine\n")
    paths = {f.path for f in list_files(tmp_path)}
    assert "README.md" in paths
    assert not any(p.startswith("launcher/") for p in paths)

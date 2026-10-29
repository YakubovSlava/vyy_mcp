"""Files at the root of the shared/child repo, managed from the admin UI.

The security-relevant half gets the most attention here: paths arriving
from a form are attacker-controlled, and this module is one `..` away from
writing outside the repo. Also pinned: `tools/` and `.git/` are untouchable,
because the registry's view of installed skills is derived from that
directory and must not drift from what's on disk.
"""
import io
import zipfile

import pytest

from jupyter_mcp_tools.common import repo_files
from jupyter_mcp_tools.common.repo_files import (
    FileValidationError,
    delete_path,
    install_archive,
    is_text_file,
    list_files,
    read_file,
    read_file_path,
    write_file,
)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "tools" / "echo").mkdir(parents=True)
    (root / "tools" / "echo" / "tool.py").write_text("def echo(): pass\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[remote]\n\turl = https://user:tok@x\n")
    (root / "README.md").write_text("# hi\n")
    return root


def _zip(tmp_path, entries: dict[str, bytes], name="up.zip", dirs=()):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        # Real archives record folder entries; the wrapping-folder rule
        # relies on them (see repo_files._common_root_dir).
        for directory in dirs:
            zf.writestr(directory, b"")
        for member, payload in entries.items():
            zf.writestr(member, payload)
    return path


# --------------------------------------------------------------------------
# path safety
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "../outside.txt",
        "src/../../outside.txt",
        "/etc/passwd",
        "tools/echo/tool.py",
        "tools",
        ".git/config",
    ],
)
def test_dangerous_or_reserved_paths_are_refused(repo, bad):
    with pytest.raises(FileValidationError):
        write_file(repo, bad, "x")
    with pytest.raises(FileValidationError):
        read_file(repo, bad)


def test_uppercase_reserved_name_is_refused(repo):
    """Case-insensitive filesystems would let .GIT through otherwise."""
    with pytest.raises(FileValidationError):
        write_file(repo, ".GIT/config", "x")


def test_symlink_escape_is_refused(repo, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("do not touch\n")
    (repo / "link.txt").symlink_to(outside)
    # Writing "through" the link would escape the repo while the path looks
    # legal -- _resolve() follows it, so containment must fail.
    with pytest.raises(FileValidationError):
        write_file(repo, "link.txt", "overwritten")


def test_writing_into_a_subdirectory_creates_it(repo):
    target = write_file(repo, "src/pkg/app.py", "print(1)\n")
    assert target.read_text() == "print(1)\n"


# --------------------------------------------------------------------------
# listing / reading
# --------------------------------------------------------------------------


def test_listing_hides_tools_and_git(repo):
    write_file(repo, "src/app.py", "x = 1\n")
    paths = {f.path for f in list_files(repo)}
    assert "README.md" in paths
    assert "src/app.py" in paths
    # The registry's own directory and git internals (which can hold
    # credentials in remote URLs) must never be exposed here.
    assert not any(p.startswith("tools") for p in paths)
    assert not any(p.startswith(".git") for p in paths)


def test_listing_marks_directories_and_editability(repo):
    (repo / "binary.bin").write_bytes(b"\x00\x01\x02")
    write_file(repo, "notes.md", "# text\n")
    by_path = {f.path: f for f in list_files(repo)}
    assert by_path["binary.bin"].editable is False
    assert by_path["notes.md"].editable is True
    assert by_path["notes.md"].is_dir is False


def test_binary_file_is_readable_for_download_but_not_for_editing(repo):
    (repo / "img.png").write_bytes(b"\x89PNG\x00\x00")
    assert is_text_file(repo / "img.png") is False
    with pytest.raises(FileValidationError):
        read_file(repo, "img.png")
    # Download path is binary-safe.
    assert read_file_path(repo, "img.png").read_bytes().startswith(b"\x89PNG")


def test_read_rejects_a_directory(repo):
    (repo / "src").mkdir()
    with pytest.raises(FileValidationError):
        read_file(repo, "src")


def test_utf8_cyrillic_is_editable(repo):
    write_file(repo, "doc.md", "# Привет\n")
    assert is_text_file(repo / "doc.md") is True
    assert read_file(repo, "doc.md") == "# Привет\n"


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------


def test_delete_file_and_directory(repo):
    write_file(repo, "src/app.py", "x\n")
    delete_path(repo, "src")
    assert not (repo / "src").exists()


def test_delete_refuses_protected_paths(repo):
    with pytest.raises(FileValidationError):
        delete_path(repo, "tools")
    assert (repo / "tools" / "echo" / "tool.py").exists()


# --------------------------------------------------------------------------
# archive upload
# --------------------------------------------------------------------------


def test_archive_unpacks_into_the_root(repo, tmp_path):
    archive = _zip(tmp_path, {"README.md": b"# new\n", "src/app.py": b"print(1)\n"})
    written = install_archive(repo, archive, overwrite=True)
    assert sorted(written) == ["README.md", "src/app.py"]
    assert (repo / "src" / "app.py").read_text() == "print(1)\n"


def test_archive_with_one_wrapping_folder_is_stripped(repo, tmp_path):
    """Right-click "Compress" on a folder nests everything one level —
    landing files one level deeper would silently break references."""
    archive = _zip(
        tmp_path,
        {"myproj/src/app.py": b"x\n", "myproj/README.md": b"y\n"},
        dirs=["myproj/"],
    )
    written = install_archive(repo, archive, overwrite=True)
    assert sorted(written) == ["README.md", "src/app.py"]
    assert (repo / "src" / "app.py").exists()


def test_archive_refuses_to_overwrite_without_permission(repo, tmp_path):
    archive = _zip(tmp_path, {"README.md": b"# clobber\n"})
    with pytest.raises(FileValidationError, match="уже существует"):
        install_archive(repo, archive, overwrite=False)
    assert (repo / "README.md").read_text() == "# hi\n"


def test_archive_cannot_write_into_tools(repo, tmp_path):
    archive = _zip(tmp_path, {"tools/evil/tool.py": b"def x(): pass\n"})
    with pytest.raises(FileValidationError):
        install_archive(repo, archive, overwrite=True)
    assert not (repo / "tools" / "evil").exists()


def test_archive_cannot_write_into_git(repo, tmp_path):
    archive = _zip(tmp_path, {".git/hooks/pre-commit": b"#!/bin/sh\n"})
    with pytest.raises(FileValidationError):
        install_archive(repo, archive, overwrite=True)


def test_archive_with_parent_traversal_is_refused(repo, tmp_path):
    archive = _zip(tmp_path, {"../../escape.txt": b"pwned"})
    with pytest.raises(FileValidationError):
        install_archive(repo, archive, overwrite=True)


def test_non_zip_is_refused(repo, tmp_path):
    plain = tmp_path / "not.zip"
    plain.write_text("nope")
    with pytest.raises(FileValidationError, match="не zip"):
        install_archive(repo, plain)


def test_empty_archive_is_refused(repo, tmp_path):
    archive = _zip(tmp_path, {})
    with pytest.raises(FileValidationError, match="пуст"):
        install_archive(repo, archive)


def test_binary_files_survive_an_archive_roundtrip(repo, tmp_path):
    payload = bytes(range(256))
    archive = _zip(tmp_path, {"assets/blob.bin": payload})
    install_archive(repo, archive, overwrite=True)
    assert (repo / "assets" / "blob.bin").read_bytes() == payload


def test_failed_archive_leaves_nothing_behind(repo, tmp_path):
    """Staged in a temp dir first, so a validation failure mid-way must not
    leave a half-written tree."""
    archive = _zip(tmp_path, {"ok.txt": b"fine\n", "tools/bad/tool.py": b"x\n"})
    with pytest.raises(FileValidationError):
        install_archive(repo, archive, overwrite=True)
    assert not (repo / "ok.txt").exists()


def test_single_file_in_a_subdirectory_is_not_flattened(repo, tmp_path):
    """Regression: a lone `assets/logo.png` used to be read as "only one
    top-level folder, so that folder is wrapping" and landed as `logo.png`.
    Flattening is unrecoverable; a file one level deeper is visible."""
    archive = _zip(tmp_path, {"assets/logo.png": b"png"})
    written = install_archive(repo, archive, overwrite=True)
    assert written == ["assets/logo.png"]
    assert (repo / "assets" / "logo.png").exists()

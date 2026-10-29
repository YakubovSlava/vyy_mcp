"""Files at the root of the shared (child) repository, managed from the UI.

The shared directory is a git repo that receives every installed skill in
`tools/`. Per explicit request, everything *besides* `tools/` is now
ordinary editable content — source files for running the server, docs,
configs, anything — uploaded as a zip, edited in place, and deleted, all
from the admin UI, so a push ships exactly what the admin put there.

`tools/` is deliberately untouchable here: it is the registry's own
directory (see `common/registry.py`), and a stray edit through a generic
file editor could leave a skill folder that no longer parses, or delete a
skill without the registry noticing. Skills are managed through the Skills
tab, where that bookkeeping happens.

Design decisions worth keeping:

- **Every path is validated, not sanitised.** A path coming from a form is
  attacker-controlled as far as this module is concerned (`..`, absolute
  paths, symlinks); each operation resolves the target and refuses
  anything that escapes the repo root or lands in `tools/`. Silently
  rewriting a rejected path would hide a real bug in the caller.
- **Symlinks are never followed when writing.** A symlink inside the repo
  pointing at `/etc/passwd` is a file that exists; writing "through" it
  would escape the root even though the path itself looks legal.
- **Binaries are supported**, so uploads are stored byte-for-byte. The UI
  decides which files it offers to edit as text (see `is_text_file`).
- **Nothing here commits or pushes.** Mutations call the registry's own
  push helper from the route layer, exactly like skill installs do, so
  there is one code path that talks to git.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

# The registry's directory. Untouchable through this module.
PROTECTED_DIR_NAME = "tools"

# .git must never be written to or read from through the UI: writing there
# is how you rewrite a repo's history/remotes, and reading it exposes
# credentials embedded in config files.
RESERVED_NAMES = frozenset({".git", PROTECTED_DIR_NAME})

# Extensions that are still "text you might want to edit" but are not
# UTF-8 decodable in the naive sense. Kept small on purpose — the check is
# a content probe, not an extension whitelist, because the point is "can a
# textarea show this without mangling it".
TEXT_PROBE_BYTES = 8192


class FileValidationError(ValueError):
    """The requested path is not allowed (escapes the root, is reserved...)."""


@dataclass
class RepoFile:
    path: str  # repo-relative, forward slashes, no leading "./"
    size: int
    is_dir: bool
    editable: bool  # text and decodable — the UI offers "Редактировать"


def _resolve(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    """Map a repo-relative path to an absolute one, or raise.

    `resolve()` is what makes `..` and symlink tricks fail rather than
    silently write somewhere unexpected; the containment check then
    asserts the result really is under `root`.
    """
    if not relative or relative.strip() in ("", "."):
        raise FileValidationError("пустой путь")

    # A leading "/" would make `root / relative` ignore the root entirely —
    # Path's own behaviour, not a guess.
    if relative.startswith("/") or relative.startswith("\\"):
        raise FileValidationError("нужен путь относительно корня репозитория")

    parts = Path(relative).parts
    if any(part in ("..", "") for part in parts):
        raise FileValidationError("недопустимый путь")

    # Case-insensitive filesystems: compare the first segment the way the
    # filesystem would, so ".GIT" can't slip past on macOS/Windows.
    if parts[0] in RESERVED_NAMES or parts[0].lower() in {n.lower() for n in RESERVED_NAMES}:
        raise FileValidationError(
            f"«{parts[0]}» — служебная папка, менять её через эту страницу нельзя"
        )

    target = (root / relative).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise FileValidationError("путь выходит за пределы репозитория")

    if must_exist and not target.exists():
        raise FileValidationError(f"файл не найден: {relative}")
    if target == root_resolved:
        raise FileValidationError("нельзя перезаписать сам корень репозитория")
    return target


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root.resolve()).as_posix()


def is_text_file(path: Path) -> bool:
    """Cheap content probe: is this something a textarea can show?

    Deliberately content- rather than extension-based. A `.conf` full of
    binary, or a `.py` with a stray NUL, both break the editor; a
    extension-less script is perfectly editable.
    """
    try:
        if path.stat().st_size > 5 * 1024 * 1024:
            # Large enough that shipping it through a textarea is a bad
            # idea regardless of content.
            return False
        with path.open("rb") as handle:
            chunk = handle.read(TEXT_PROBE_BYTES)
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        # A decode error past the probe boundary (a multi-byte char split
        # across it) is not a binary file; only fail on the chunk as a
        # whole being non-text.
        return _decodes_as_utf8(path)
    return True


def _decodes_as_utf8(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    return True
                chunk.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False


def list_files(root: Path) -> list[RepoFile]:
    """Everything at the repo root except `tools/` and `.git/`.

    Recursive: a "source tree for running the server" is nested by nature,
    so a flat listing would be useless. Sorted directories-first then by
    name, which is how the page renders it.
    """
    if not root.is_dir():
        return []

    found: list[RepoFile] = []
    for entry in sorted(root.rglob("*")):
        relative = _relative(root, entry)
        first = relative.split("/", 1)[0]
        if first in (PROTECTED_DIR_NAME, ".git"):
            continue
        # Skips anything reached only through a symlink to outside the
        # root: rglob follows directories, and `..`-style names can't
        # appear here, but a symlinked dir would.
        try:
            entry.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        if entry.is_symlink():
            continue
        found.append(
            RepoFile(
                path=relative,
                size=0 if entry.is_dir() else entry.stat().st_size,
                is_dir=entry.is_dir(),
                editable=entry.is_file() and is_text_file(entry),
            )
        )
    found.sort(key=lambda f: (not f.is_dir, f.path.lower()))
    return found


def read_file(root: Path, relative: str) -> str:
    target = _resolve(root, relative, must_exist=True)
    if target.is_dir():
        raise FileValidationError(f"«{relative}» — это папка")
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FileValidationError(
            f"«{relative}» не текст в UTF-8 — редактировать нельзя, только скачать"
        ) from exc


def read_file_path(root: Path, relative: str) -> Path:
    """Resolve a path for binary-safe reads (downloads).

    Separate from `read_file` because that one is for the editor and must
    stay text-only — a download of a binary must not go through a UTF-8
    decode at all.
    """
    target = _resolve(root, relative, must_exist=True)
    if target.is_dir() or target.is_symlink():
        raise FileValidationError(f"«{relative}» — не файл")
    return target


def write_file(root: Path, relative: str, content: str) -> Path:
    """Create or overwrite a single file, creating parent dirs as needed."""
    target = _resolve(root, relative)
    if target.is_dir():
        raise FileValidationError(f"«{relative}» — это папка, не файл")
    if target.is_symlink():
        # `_resolve` already resolved symlinks, so reaching here means the
        # path itself was one; refusing avoids writing through it.
        raise FileValidationError("нельзя писать через символическую ссылку")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def delete_path(root: Path, relative: str) -> None:
    target = _resolve(root, relative, must_exist=True)
    if target.is_symlink():
        target.unlink()
        return
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def install_archive(root: Path, archive_path: Path, *, overwrite: bool = False) -> list[str]:
    """Unpack a zip into the repo root as ordinary files.

    Unpacked in a temp directory first and validated there, so a hostile or
    simply broken archive can't leave a half-written tree behind: nothing
    touches the real root until every member has been checked.

    A single wrapping folder is stripped, same rule the skill uploader
    uses (`registry._common_root_dir`) — zipping a *folder* is the normal
    result of right-click → "Compress", and landing `src/...` one level
    deeper than expected would silently break whatever references it.
    """
    if not zipfile.is_zipfile(archive_path):
        raise FileValidationError("это не zip-архив")

    with zipfile.ZipFile(archive_path) as archive:
        names = [n for n in archive.namelist() if n and not n.endswith("/")]
        if not names:
            raise FileValidationError("архив пуст")
        _reject_unsafe_members(names)

        prefix = _common_root_dir(archive.infolist())
        # A wrapping folder must never be stripped down to `tools/` or
        # `.git/`: an archive of `tools/evil/tool.py` would otherwise be
        # rewritten to `evil/tool.py`, quietly bypassing the reserved-name
        # check below.
        if prefix and prefix.rstrip("/") in RESERVED_NAMES:
            prefix = ""
        members: list[tuple[str, zipfile.ZipInfo]] = []
        for info in archive.infolist():
            if not info.filename or info.filename.endswith("/"):
                continue
            stripped = info.filename[len(prefix) :] if prefix else info.filename
            if stripped:
                members.append((stripped, info))
        if not members:
            raise FileValidationError("архив пуст")

        for name, _info in members:
            # Same protection as a single-file write: the archive must not
            # be able to place anything in tools/ or .git/.
            _resolve(root, name)

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            for name, info in members:
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Read/write by hand rather than `extractall`: it neither
                # honours our validation above nor is safe against zip-slip
                # on its own (older Pythons especially).
                with archive.open(info) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)

            written: list[str] = []
            for name, _info in members:
                source = staging / name
                target = _resolve(root, name)
                if target.exists() and not overwrite:
                    raise FileValidationError(
                        f"«{name}» уже существует — включите перезапись или удалите его"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    shutil.rmtree(target) if target.is_dir() else target.unlink()
                shutil.move(str(source), str(target))
                written.append(name)
            return written


def _reject_unsafe_members(names: list[str]) -> None:
    for name in names:
        parts = Path(name).parts
        if name.startswith("/") or name.startswith("\\") or ".." in parts:
            raise FileValidationError(f"архив содержит недопустимый путь: {name}")


def _common_root_dir(infos: list[zipfile.ZipInfo]) -> str:
    """Prefix of the single wrapping folder, or "".

    Signal used: **explicit directory entries** in the archive. A folder
    zipped by any normal tool ("Compress", `zip -r`) records `myproj/`
    itself, so its presence at the archive root means the whole thing is
    one folder and should be stripped.

    Deliberately *not* inferred from "all files happen to share a first
    path segment": with a single file like `assets/logo.png` that rule
    cannot tell a wrapping folder from a real subdirectory, and it guessed
    "wrapping folder" — silently flattening `assets/logo.png` to
    `logo.png`. Skipping the strip is the recoverable failure (a file one
    level deeper, visible in the UI); flattening is not.
    """
    top_level_dirs = {
        info.filename.split("/", 1)[0]
        for info in infos
        if info.filename.endswith("/") and info.filename.strip("/")
    }
    if len(top_level_dirs) != 1:
        return ""
    return next(iter(top_level_dirs)) + "/"

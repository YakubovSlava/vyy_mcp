"""Code-skill format and validation.

A code skill is a single Python file (any filename) with exactly one
top-level function — no separate manifest.json. The function itself *is*
the manifest:

    def greet(name: str) -> str:
        '''Greets someone by name.'''
        return f"Hello, {name}!"

- the function's **name** becomes the skill's name (must match
  ``[a-z0-9][a-z0-9_-]{1,63}``, which any normal snake_case Python
  function name already does)
- its **docstring** becomes the skill's description
- ``"<module>:<function>"`` is computed automatically as the entrypoint
- an optional module-level ``__version__ = "..."`` string literal sets
  the version; it defaults to "1.0.0" if absent
- the MCP input schema is inferred from the function's type hints, same
  as any other FastMCP tool — there's nothing else to declare

Validation is deliberately lightweight (structure + static AST checks) and
never executes code from the archive, matching the "trusted internal
server" threat model this project targets.
"""
from __future__ import annotations

import ast
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")

DEFAULT_VERSION = "1.0.0"


class ToolValidationError(ValueError):
    """Raised when a skill fails validation."""


@dataclass
class Manifest:
    name: str
    version: str
    description: str
    entrypoint: str

    @property
    def module_name(self) -> str:
        return self.entrypoint.split(":", 1)[0]

    @property
    def function_name(self) -> str:
        return self.entrypoint.split(":", 1)[1]

    @property
    def kind(self) -> str:
        return "code"


def root_python_files(names: list[str]) -> list[str]:
    """Top-level (not nested in a folder) ``.py`` filenames among `names`."""
    return [n for n in names if n.endswith(".py") and "/" not in n]


def _check_safe_members(zf: zipfile.ZipFile) -> None:
    for member in zf.namelist():
        if member.startswith("/") or ".." in Path(member).parts:
            raise ToolValidationError(f"archive contains unsafe path: {member}")


def _extract_version(tree: ast.Module) -> str:
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "__version__"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                return node.value.value
    return DEFAULT_VERSION


def _manifest_from_source(filename: str, source: bytes | str) -> Manifest:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise ToolValidationError(f"'{filename}' has a syntax error: {exc}") from exc

    functions = [
        node
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not functions:
        raise ToolValidationError(f"'{filename}' defines no top-level function to use as the skill")
    if len(functions) > 1:
        found = ", ".join(f.name for f in functions)
        raise ToolValidationError(
            f"'{filename}' must define exactly one top-level function to be used as a skill (found: {found})"
        )
    func = functions[0]

    if not NAME_RE.match(func.name):
        raise ToolValidationError(
            f"function name '{func.name}' must match [a-z0-9][a-z0-9_-]{{1,63}} to be used as the skill name"
        )

    description = ast.get_docstring(func)
    if not description:
        raise ToolValidationError(f"function '{func.name}' needs a docstring — it becomes the skill's description")

    return Manifest(
        name=func.name,
        version=_extract_version(tree),
        description=description.strip(),
        entrypoint=f"{filename[:-3]}:{func.name}",
    )


def validate_archive(archive_path: Path) -> Manifest:
    """Validate a code-skill `.zip` and return its manifest.

    Raises ToolValidationError on any structural problem. Never executes
    code contained in the archive.
    """
    if not zipfile.is_zipfile(archive_path):
        raise ToolValidationError("file is not a valid zip archive")

    with zipfile.ZipFile(archive_path) as zf:
        _check_safe_members(zf)
        candidates = root_python_files(zf.namelist())
        if not candidates:
            raise ToolValidationError("archive contains no .py file at its root")
        if len(candidates) > 1:
            raise ToolValidationError(
                f"archive must contain exactly one .py file at its root, found: {', '.join(sorted(candidates))}"
            )
        filename = candidates[0]
        source = zf.read(filename)

    return _manifest_from_source(filename, source)


def validate_dir(skill_dir: Path) -> Manifest:
    """Validate an already-installed code-skill folder on disk (used when
    re-reading an installed skill, not an upload)."""
    candidates = sorted(p.name for p in skill_dir.iterdir() if p.is_file() and p.suffix == ".py")
    if not candidates:
        raise ToolValidationError("skill folder contains no .py file at its root")
    if len(candidates) > 1:
        raise ToolValidationError(
            f"skill folder must contain exactly one .py file at its root, found: {', '.join(candidates)}"
        )
    filename = candidates[0]
    source = (skill_dir / filename).read_text(encoding="utf-8")
    return _manifest_from_source(filename, source)

"""Tool archive format and validation.

A tool archive is a zip file with this layout::

    manifest.json
    <module>.py        # module referenced by manifest's "entrypoint"
    ... (optional extra files used by the module)

manifest.json fields:
    name          - unique slug, [a-z0-9][a-z0-9_-]*
    version       - free-form string, e.g. "1.0.0"
    description   - human readable description
    entrypoint    - "<module>:<function>", module must be a top-level .py
                    file in the archive and function a top-level def in it
    parameters    - optional JSON schema (object) describing the function's
                    keyword arguments; defaults to a permissive object schema

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
from typing import Any

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class ToolValidationError(ValueError):
    """Raised when an archive fails validation."""


@dataclass
class Manifest:
    name: str
    version: str
    description: str
    entrypoint: str
    parameters: dict[str, Any]

    @property
    def module_name(self) -> str:
        return self.entrypoint.split(":", 1)[0]

    @property
    def function_name(self) -> str:
        return self.entrypoint.split(":", 1)[1]

    @property
    def kind(self) -> str:
        return "code"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        missing = [f for f in ("name", "version", "description", "entrypoint") if f not in data]
        if missing:
            raise ToolValidationError(f"manifest.json missing field(s): {', '.join(missing)}")

        name = data["name"]
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ToolValidationError(
                "manifest 'name' must match [a-z0-9][a-z0-9_-]{1,63}"
            )

        entrypoint = data["entrypoint"]
        if not isinstance(entrypoint, str) or ":" not in entrypoint:
            raise ToolValidationError("manifest 'entrypoint' must look like 'module:function'")

        parameters = data.get("parameters", {"type": "object", "properties": {}})
        if not isinstance(parameters, dict):
            raise ToolValidationError("manifest 'parameters' must be a JSON schema object")

        return cls(
            name=name,
            version=str(data["version"]),
            description=str(data["description"]),
            entrypoint=entrypoint,
            parameters=parameters,
        )


def _read_manifest(zf: zipfile.ZipFile) -> Manifest:
    import json

    try:
        raw = zf.read("manifest.json")
    except KeyError as exc:
        raise ToolValidationError("archive is missing manifest.json at its root") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolValidationError(f"manifest.json is not valid JSON: {exc}") from exc

    return Manifest.from_dict(data)


def _check_entrypoint_exists(zf: zipfile.ZipFile, manifest: Manifest) -> None:
    module_path = f"{manifest.module_name}.py"
    try:
        source = zf.read(module_path)
    except KeyError as exc:
        raise ToolValidationError(
            f"entrypoint module '{module_path}' not found in archive"
        ) from exc

    try:
        tree = ast.parse(source, filename=module_path)
    except SyntaxError as exc:
        raise ToolValidationError(f"entrypoint module '{module_path}' has a syntax error: {exc}") from exc

    top_level_functions = {
        node.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if manifest.function_name not in top_level_functions:
        raise ToolValidationError(
            f"function '{manifest.function_name}' not defined at top level of '{module_path}'"
        )


def _check_safe_members(zf: zipfile.ZipFile) -> None:
    for member in zf.namelist():
        if member.startswith("/") or ".." in Path(member).parts:
            raise ToolValidationError(f"archive contains unsafe path: {member}")


def validate_archive(archive_path: Path) -> Manifest:
    """Validate a tool archive and return its manifest.

    Raises ToolValidationError on any structural problem. Never executes
    code contained in the archive.
    """
    if not zipfile.is_zipfile(archive_path):
        raise ToolValidationError("file is not a valid zip archive")

    with zipfile.ZipFile(archive_path) as zf:
        _check_safe_members(zf)
        manifest = _read_manifest(zf)
        _check_entrypoint_exists(zf, manifest)

    return manifest

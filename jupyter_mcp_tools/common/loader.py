"""Dynamic import of installed tool modules."""
from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

from .manifest import Manifest
from .registry import tools_dir


def load_entrypoint(shared_dir: Path, manifest: Manifest) -> Callable[..., Any]:
    """Import a tool's module from disk and return its entrypoint callable.

    Each call uses a unique module name so repeated loads (e.g. after a
    tool was reinstalled) never hit a stale sys.modules cache.
    """
    module_path = tools_dir(shared_dir) / manifest.name / f"{manifest.module_name}.py"
    if not module_path.exists():
        raise FileNotFoundError(f"entrypoint module not found: {module_path}")

    unique_name = f"jupyter_mcp_tools.installed.{manifest.name}.{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(unique_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load module spec for {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(unique_name, None)

    try:
        return getattr(module, manifest.function_name)
    except AttributeError as exc:
        raise AttributeError(
            f"module '{manifest.module_name}' has no function '{manifest.function_name}'"
        ) from exc

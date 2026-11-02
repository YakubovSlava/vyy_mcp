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
        # Suppress bytecode writing for this import. Loading a skill would
        # otherwise make CPython drop `__pycache__/` *inside the skill's own
        # folder* -- which lives in the shared git repo, so it gets committed
        # and cloned out to everyone (see common/git_push.py). The push step
        # also filters it out, but not creating it is the better fix: nothing
        # to ignore, nothing to leak, and no stale `.pyc` that a later import
        # could prefer over updated source.
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
    finally:
        sys.modules.pop(unique_name, None)

    try:
        return getattr(module, manifest.function_name)
    except AttributeError as exc:
        raise AttributeError(
            f"module '{manifest.module_name}' has no function '{manifest.function_name}'"
        ) from exc

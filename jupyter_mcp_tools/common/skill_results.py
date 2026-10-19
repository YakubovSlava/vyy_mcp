"""Convention for a skill that hands back files for a human to run
themselves, rather than a plain text answer.

A skill's entrypoint function can return any JSON-serializable value —
FastMCP serializes it straight into the MCP response (see PLAN.md: "input
schema comes from Python type hints," the same mechanism that makes this
work with zero server-side code). This module just standardizes the
*shape* of that JSON for the "here are some files, run them under your
own account" case, so every skill that does this looks the same to
whoever (human or agent) is driving the MCP client.

The MCP server never executes anything here — it only returns content.
Running the files is explicitly the calling human's action, under their
own credentials/environment, same as the Bitbucket connection notebook.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class SkillFile:
    filename: str
    content: str
    run_hint: str | None = None  # e.g. "bash setup.sh" — how a human would run it


def files_result(message: str, files: list[SkillFile]) -> dict:
    """Build the standard {"message": ..., "files": [...]} shape.

    Example entrypoint using this:

        from jupyter_mcp_tools.common.skill_results import SkillFile, files_result

        def make_setup(name: str) -> dict:
            return files_result(
                f"Setup files for {name} — download and run these yourself.",
                [
                    SkillFile("setup.sh", "#!/bin/bash\\necho hi\\n", run_hint="bash setup.sh"),
                    SkillFile("config.yaml", f"name: {name}\\n"),
                ],
            )
    """
    return {"message": message, "files": [asdict(f) for f in files]}

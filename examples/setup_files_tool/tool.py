from jupyter_mcp_tools.common.skill_results import SkillFile, files_result


def make_setup_files(project_name: str) -> dict:
    """Returns multiple files for the caller to save and run locally —
    the MCP server never executes any of this itself."""
    return files_result(
        f"Setup files for '{project_name}'. Save each one and run them yourself.",
        [
            SkillFile(
                filename="setup.sh",
                content=f"#!/bin/bash\nset -euo pipefail\necho 'Setting up {project_name}...'\n",
                run_hint="bash setup.sh",
            ),
            SkillFile(
                filename="config.yaml",
                content=f"project: {project_name}\n",
            ),
        ],
    )

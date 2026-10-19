"""Builds notebooks/connect_to_mcp.ipynb — the single file distributed to
every end user (see scripts/build_connect_notebook.py).

The notebook, run on the end user's own JupyterLab:

1. Resolves the current MCP host address by running the admin-configured
   Bitbucket host-lookup script locally (username as an argument,
   password on stdin) — this is how the notebook avoids ever hardcoding a
   host address that could go stale.
2. Prints the MCP connection command, with the login field filled in from
   the current OS user (`getpass.getuser()`).
3. Self-registers with the main host (over `curl`, like every other
   main<->client call in this project) so the admin sees this user show
   up in the UI without waiting for their first real MCP call.

MAIN_HOST and BITBUCKET_SCRIPT_PATH are baked in as plain constants in
the notebook's first cell at generation time — re-run
scripts/build_connect_notebook.py (or hand-edit those two lines directly
in the .ipynb) if either changes.
"""
from __future__ import annotations

import json


def build_notebook(
    *,
    bitbucket_script_path: str,
    main_host_base_url: str,
    mcp_connect_url: str = "/mcp",
) -> dict:
    register_url = f"{main_host_base_url.rstrip('/')}/api/users/register"

    source_config = f"""\
# Edit these two lines (or re-run scripts/build_connect_notebook.py) if
# either ever changes — everything below reads from these constants.
MAIN_HOST = {main_host_base_url!r}
BITBUCKET_SCRIPT_PATH = {bitbucket_script_path!r}
"""

    source_lookup = """\
import getpass
import subprocess
import sys

bb_username = input("Bitbucket username: ")
bb_password = getpass.getpass("Bitbucket password: ")

result = subprocess.run(
    [sys.executable, BITBUCKET_SCRIPT_PATH, bb_username],
    input=bb_password,
    capture_output=True,
    text=True,
)
if result.returncode != 0:
    raise RuntimeError(f"Could not resolve the MCP host address: {result.stderr.strip()}")

host_address = result.stdout.strip()
print("Resolved MCP host:", host_address)
"""

    source_connect = f"""\
import getpass
import subprocess

os_username = getpass.getuser()

connection_command = (
    f"claude mcp add shared-tools --transport http {{host_address}}{mcp_connect_url} "
    f'--header "X-MCP-User: {{os_username}}"'
)
print("\\nRun this command to connect to the shared MCP server:\\n")
print(connection_command)

subprocess.run(
    ["curl", "-s", "-X", "POST", {register_url!r}, "-d", f"username={{os_username}}"],
    capture_output=True,
)
print("\\n(You've been registered in the admin UI's user list.)")
"""

    return {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "# Connect to the shared MCP server\n",
                    "\n",
                    "Run all cells below in order. The first holds this notebook's "
                    "configuration; the second resolves the current MCP host address "
                    "via Bitbucket; the third prints the command you run to connect "
                    "your MCP client.",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": source_config.splitlines(keepends=True),
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": source_lookup.splitlines(keepends=True),
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": source_connect.splitlines(keepends=True),
            },
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def build_notebook_json(**kwargs) -> str:
    return json.dumps(build_notebook(**kwargs), indent=2)

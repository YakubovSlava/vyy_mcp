"""Builds notebooks/connect_to_mcp.ipynb — the single file distributed to
every end user (see scripts/build_connect_notebook.py).

The notebook, run on the end user's own JupyterLab:

1. Resolves the current MCP host address by downloading a raw file from a
   Bitbucket Server/Data Center repo over its REST API (Basic Auth with
   the user's own Bitbucket username/password or personal access token) —
   this is how the notebook avoids ever hardcoding a host address that
   could go stale. The file's project/repo/path are plain constants in
   the first cell; edit them (or BITBUCKET_BASE_URL) if your setup is
   different, e.g. a wiki page instead of a repo file.
2. Prints the MCP connection command for both Claude Code and opencode,
   with the login field filled in from the current OS user
   (`getpass.getuser()`).
3. Self-registers with the main host (over `curl`, like every other
   main<->client call in this project) so the admin sees this user show
   up in the UI without waiting for their first real MCP call.

MAIN_HOST and the BITBUCKET_* constants are baked into the notebook's
first cell at generation time — re-run scripts/build_connect_notebook.py
(or hand-edit those lines directly in the .ipynb) if any of them change.
"""
from __future__ import annotations

import json


def build_notebook(
    *,
    main_host_base_url: str,
    bitbucket_base_url: str,
    bitbucket_project: str = "PROJ",
    bitbucket_repo: str = "mcp-config",
    bitbucket_file_path: str = "host.txt",
    mcp_connect_url: str = "/mcp",
) -> dict:
    register_url = f"{main_host_base_url.rstrip('/')}/api/users/register"

    source_config = f"""\
# Edit these (or re-run scripts/build_connect_notebook.py) if any of them
# ever change — everything below reads from these constants.
MAIN_HOST = {main_host_base_url!r}

# Where the current MCP host address lives in Bitbucket (Bitbucket
# Server/Data Center REST API: BITBUCKET_BASE_URL/rest/api/1.0/...).
# Change BITBUCKET_PROJECT/BITBUCKET_REPO/BITBUCKET_FILE_PATH to point at
# whatever file in whatever repo your org actually keeps this in.
BITBUCKET_BASE_URL = {bitbucket_base_url!r}
BITBUCKET_PROJECT = {bitbucket_project!r}
BITBUCKET_REPO = {bitbucket_repo!r}
BITBUCKET_FILE_PATH = {bitbucket_file_path!r}
"""

    source_lookup = """\
import getpass

import requests

bb_username = input("Bitbucket username: ")
bb_password = getpass.getpass("Bitbucket password (or personal access token): ")

raw_url = (
    f"{BITBUCKET_BASE_URL.rstrip('/')}/rest/api/1.0/projects/{BITBUCKET_PROJECT}"
    f"/repos/{BITBUCKET_REPO}/raw/{BITBUCKET_FILE_PATH}"
)
response = requests.get(raw_url, auth=(bb_username, bb_password))
response.raise_for_status()

host_address = response.text.strip()
print("Resolved MCP host:", host_address)
"""

    source_connect = f"""\
import getpass
import subprocess

os_username = getpass.getuser()
mcp_url = f"{{host_address}}{mcp_connect_url}"

claude_command = (
    f'claude mcp add shared-tools --transport http {{mcp_url}} '
    f'--header "X-MCP-User: {{os_username}}"'
)
opencode_command = (
    f'opencode mcp add shared-tools --url {{mcp_url}} '
    f'--header "X-MCP-User={{os_username}}"'
)

print("\\nTo connect with Claude Code, run:\\n")
print(claude_command)
print("\\nTo connect with opencode, run:\\n")
print(opencode_command)

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
                    "from Bitbucket; the third prints the commands you run to connect "
                    "your MCP client (Claude Code or opencode).",
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

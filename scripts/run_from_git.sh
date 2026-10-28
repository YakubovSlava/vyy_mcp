#!/usr/bin/env bash
# Zero-install launcher for the MCP server, meant to go straight into an
# MCP client's own config (Claude Desktop, Claude Code, ...) as the
# "command" to run — no admin UI, no hosted streamable-http server, no
# manual `pip install` step. The client spawns this script itself and
# talks to it over stdio, exactly like an `npx`-launched Node MCP server,
# just for this Python project.
#
# What it does, every time it's launched:
#   1. Clone this repo into a local cache dir (or `git pull` if already
#      cloned there).
#   2. Create a venv inside that cache dir (first run only) and
#      `pip install -e` the project into it.
#   3. Run `jupyter-mcp-tools-server --transport stdio --shared-dir
#      <clone>/shared_tools` — stdio means the server never binds a
#      port or needs to be "hosted" anywhere; it just lives for the
#      duration of this one client session, exec'd on demand.
#
# `shared_tools/` is a ready-to-serve copy of this repo's example
# skills, committed directly in the repo (see PLAN.md -> "A git-clone-
# and-run distribution path, no hosting needed") — so a fresh clone
# always has something to serve, with no separate admin UI step
# required first.
#
# MCP client config example (Claude Desktop / Claude Code):
#
#   {
#     "mcpServers": {
#       "jupyter-mcp-tools": {
#         "command": "bash",
#         "args": ["-c", "d=\"$HOME/.cache/jupyter-mcp-tools-src\"; [ -d \"$d/.git\" ] || git clone -q https://github.com/YakubovSlava/vyy_mcp.git \"$d\"; exec bash \"$d/scripts/run_from_git.sh\""]
#       }
#     }
#   }
#
# Override the source/cache location or repo URL with
# JUPYTER_MCP_TOOLS_REPO_URL / JUPYTER_MCP_TOOLS_CACHE_DIR if needed.
set -euo pipefail

REPO_URL="${JUPYTER_MCP_TOOLS_REPO_URL:-https://github.com/YakubovSlava/vyy_mcp.git}"
CACHE_DIR="${JUPYTER_MCP_TOOLS_CACHE_DIR:-$HOME/.cache/jupyter-mcp-tools-src}"

if [ -d "$CACHE_DIR/.git" ]; then
  git -C "$CACHE_DIR" pull --ff-only -q
else
  git clone -q "$REPO_URL" "$CACHE_DIR"
fi

VENV_DIR="$CACHE_DIR/.run-venv"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install -q -e "$CACHE_DIR"

exec "$VENV_DIR/bin/jupyter-mcp-tools-server" \
  --transport stdio \
  --shared-dir "$CACHE_DIR/shared_tools" \
  "$@"

#!/bin/sh
# Zero-install launcher for the MCP server, meant to go straight into an
# MCP client's own config (Claude Desktop, Claude Code, opencode, ...) as
# the "command" to run -- no admin UI, no hosted streamable-http server,
# no manual install step. The client spawns this script itself and talks
# to it over stdio, the same way an `npx`-launched Node MCP server works,
# just for this Python project. See run_from_git.bat for the Windows
# equivalent.
#
# What it does, every time it's launched:
#   1. Clone this repo into a local cache dir (or `git pull` if already
#      cloned there).
#   2. Use `uv` if it's already on PATH; otherwise install it with its
#      official standalone installer (not pip -- a prebuilt binary, no
#      Python dependency chain) and use that.
#   3. Run `uvx --from <clone> jupyter-mcp-tools-server --transport
#      stdio --shared-dir <clone>/shared_tools`. `uvx` resolves and
#      installs this project's dependencies into its own managed,
#      cached environment (far faster than a hand-rolled `python -m
#      venv` + `pip install` on repeat runs) -- stdio means the server
#      never binds a port or needs to be "hosted" anywhere; it just
#      lives for the duration of this one client session.
#
# `shared_tools/` is a ready-to-serve copy of this repo's example
# skills, committed directly in the repo (see PLAN.md -> "A git-clone-
# and-run distribution path, no hosting needed") -- so a fresh clone
# always has something to serve, with no separate admin UI step
# required first.
#
# MCP client config example (Claude Desktop / Claude Code / opencode):
#
#   {
#     "mcpServers": {
#       "jupyter-mcp-tools": {
#         "command": "sh",
#         "args": ["-c", "d=\"$HOME/.cache/jupyter-mcp-tools-src\"; [ -d \"$d/.git\" ] || git clone -q https://github.com/YakubovSlava/vyy_mcp.git \"$d\"; exec sh \"$d/scripts/run_from_git.sh\""]
#       }
#     }
#   }
#
# Override the source/cache location or repo URL with
# JUPYTER_MCP_TOOLS_REPO_URL / JUPYTER_MCP_TOOLS_CACHE_DIR if needed.
set -eu

REPO_URL="${JUPYTER_MCP_TOOLS_REPO_URL:-https://github.com/YakubovSlava/vyy_mcp.git}"
CACHE_DIR="${JUPYTER_MCP_TOOLS_CACHE_DIR:-$HOME/.cache/jupyter-mcp-tools-src}"

if [ -d "$CACHE_DIR/.git" ]; then
  git -C "$CACHE_DIR" pull --ff-only -q
else
  git clone -q "$REPO_URL" "$CACHE_DIR"
fi

if command -v uvx >/dev/null 2>&1; then
  UVX_BIN="uvx"
else
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
  UVX_BIN="$HOME/.local/bin/uvx"
fi

exec "$UVX_BIN" --from "$CACHE_DIR" jupyter-mcp-tools-server \
  --transport stdio \
  --shared-dir "$CACHE_DIR/shared_tools" \
  "$@"

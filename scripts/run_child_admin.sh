#!/usr/bin/env bash
# Launcher for a child admin's local MJApp relay.
#
# A child admin doesn't have their own shared tools directory — every
# action in the UI is forwarded to the main host (the one with the real
# shared directory) over `curl`, authenticated with the login/password
# the main admin gave them (see README.md -> "Main admin: issuing
# child-admin accounts").
#
# Usage:
#   scripts/run_child_admin.sh <main-host-url> <username> [port]
#
# Example:
#   scripts/run_child_admin.sh https://main.example.org:8766 alice 8766
#
# Then open it from your own JupyterLab the same way as the main host's
# admin UI (see README.md -> "Open the admin UI from JupyterLab"), e.g.
# at <your-jupyterhub-base>/user/<you>/proxy/<port>.
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <main-host-url> <username> [port]" >&2
  exit 1
fi

MAIN_HOST="$1"
USERNAME="$2"
PORT="${3:-8766}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -t 0 ]; then
  read -r -s -p "Password for ${USERNAME}@${MAIN_HOST}: " PASSWORD
  echo
else
  # Non-interactive (e.g. invoked from another script): read from stdin.
  read -r PASSWORD
fi

export MCP_TOOLS_MAIN_HOST="$MAIN_HOST"
export MCP_TOOLS_ADMIN_USER="$USERNAME"
export MCP_TOOLS_ADMIN_PASSWORD="$PASSWORD"
# Set to 1 if the main host uses a self-signed TLS certificate.
export MCP_TOOLS_INSECURE_TLS="${MCP_TOOLS_INSECURE_TLS:-0}"

cd "$SCRIPT_DIR"
exec uvicorn jupyter_mcp_tools.admin_ui.main:app --host 127.0.0.1 --port "$PORT"

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
  echo "  example: $0 https://main.example.org:8766 alice 8766" >&2
  exit 1
fi

MAIN_HOST="$1"
USERNAME="$2"
PORT="${3:-8766}"

# Normalise before anything else uses it. Without this, a value copied with
# surrounding quotes, a trailing space or no scheme reached `curl` unchanged
# and it failed at the first request with "URL using bad/illegal format or
# missing URL" — an error that names neither the variable nor the bad value.
MAIN_HOST="${MAIN_HOST%\"}"; MAIN_HOST="${MAIN_HOST#\"}"
MAIN_HOST="${MAIN_HOST%\'}"; MAIN_HOST="${MAIN_HOST#\'}"
MAIN_HOST="$(printf '%s' "$MAIN_HOST" | tr -d '[:space:]')"
MAIN_HOST="${MAIN_HOST%/}"

if [ -z "$MAIN_HOST" ]; then
  echo "error: main host URL is empty" >&2
  exit 1
fi
case "$MAIN_HOST" in
  http://*|https://*) ;;
  *)
    echo "error: main host must start with http:// or https://, got: '$1'" >&2
    echo "       e.g. https://main.example.org:8766" >&2
    exit 1
    ;;
esac

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

#!/bin/sh
# Thin launcher for a checkout that `jupyter-mcp-tools-setup` has already
# configured. This is the script an MCP client should point its "command" at.
#
# It is deliberately a wrapper around `jupyter_mcp_tools.launcher` rather
# than logic of its own: the Python module handles the cache lock, the
# best-effort `git pull`, and the stamp-based install check, and it is the
# same code on Windows (see run_local.bat). Nothing here may print to
# stdout — stdout is the MCP JSON-RPC stream.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(dirname -- "$HERE")

# 1. The checkout's own venv, when setup created one.
if [ -x "$ROOT/.venv/bin/python" ]; then
  exec "$ROOT/.venv/bin/python" -m jupyter_mcp_tools.launcher "$@"
fi

# 2. No venv (setup --skip-install, or the user deleted it). Running the
#    system interpreter would fail on `No module named jupyter_mcp_tools` —
#    the package is not installed anywhere — so go through uvx, which
#    resolves the project from this checkout into its own managed env.
if command -v uvx >/dev/null 2>&1; then
  exec uvx --from "$ROOT" jupyter-mcp-tools-launch "$@"
fi
if [ -x "$HOME/.local/bin/uvx" ]; then
  exec "$HOME/.local/bin/uvx" --from "$ROOT" jupyter-mcp-tools-launch "$@"
fi

# 3. Last resort: a system-wide install of this package, if there is one.
if command -v python3 >/dev/null 2>&1 && python3 -c 'import jupyter_mcp_tools' 2>/dev/null; then
  exec python3 -m jupyter_mcp_tools.launcher "$@"
fi

echo "jupyter-mcp-tools: не найдено ни venv, ни uvx, ни установленного пакета." >&2
echo "Запустите один раз: python3 $ROOT/scripts/bootstrap.py" >&2
exit 1

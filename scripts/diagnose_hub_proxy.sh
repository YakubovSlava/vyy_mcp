#!/usr/bin/env bash
# Run this ON THE SINGLE-USER SERVER (inside your own notebook server on
# prod, e.g. via a terminal opened from JupyterLab itself, or wherever
# that process actually runs) to diagnose why
# /user/<username>/proxy/<port> isn't reaching the admin UI.
#
# Usage: bash diagnose_hub_proxy.sh <admin-ui-port>
set -uo pipefail

PORT="${1:-8766}"

echo "=== 1. Python / venv in use ==="
which python3
python3 -c "import sys; print(sys.prefix)"

echo
echo "=== 2. Is jupyter-server-proxy installed in THIS python? ==="
python3 -c "import jupyter_server_proxy; print('OK:', jupyter_server_proxy.__file__)" 2>&1

echo
echo "=== 3. Is jupyter-server-proxy an ENABLED server extension? ==="
jupyter server extension list 2>&1 | grep -A2 -i proxy || echo "NOT FOUND in extension list"

echo
echo "=== 4. JupyterHub single-user env vars (confirms Hub actually spawned this) ==="
env | grep -E "JUPYTERHUB_SERVICE_PREFIX|JUPYTERHUB_BASE_URL|JUPYTERHUB_USER" || echo "none set — this process was NOT spawned by JupyterHub!"

echo
echo "=== 5. Is the admin UI actually reachable from here, locally? ==="
curl -s -o /dev/null -w "localhost:%{http_code} -> HTTP %{http_code}\n" "http://127.0.0.1:${PORT}/login" || echo "curl failed to connect"

echo
echo "=== 6. What does the real proxied path return, from this process's own perspective? ==="
PREFIX="${JUPYTERHUB_SERVICE_PREFIX:-/}"
PREFIX="${PREFIX%/}"
curl -sk -D - -o /dev/null "http://127.0.0.1:8888${PREFIX}/proxy/${PORT}/login" 2>&1 || echo "curl failed"

echo
echo "=== Done. Paste all of the above. ==="

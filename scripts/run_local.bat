@echo off
rem Thin launcher for a checkout that jupyter-mcp-tools-setup has already
rem configured. The Windows twin of scripts\run_local.sh -- point an MCP
rem client's "command" at this file.
rem
rem All the real work (cache lock, best-effort git pull, stamp-based install
rem check) lives in jupyter_mcp_tools\launcher.py, so both platforms run
rem identical logic. Nothing here writes to stdout: stdout is the MCP
rem JSON-RPC stream.
setlocal

set "ROOT=%~dp0.."
set "PY=%ROOT%\.venv\Scripts\python.exe"

rem 1. The checkout's own venv, when setup created one.
if exist "%PY%" (
  "%PY%" -m jupyter_mcp_tools.launcher %*
  exit /b %ERRORLEVEL%
)

rem 2. No venv: go through uvx, which resolves the project into its own
rem    managed env. A bare system interpreter would fail on
rem    "No module named jupyter_mcp_tools".
where uvx >nul 2>nul
if not errorlevel 1 (
  uvx --from "%ROOT%" jupyter-mcp-tools-launch %*
  exit /b %ERRORLEVEL%
)
if exist "%USERPROFILE%\.local\bin\uvx.exe" (
  "%USERPROFILE%\.local\bin\uvx.exe" --from "%ROOT%" jupyter-mcp-tools-launch %*
  exit /b %ERRORLEVEL%
)

rem 3. Last resort: a system-wide install of this package.
python -c "import jupyter_mcp_tools" >nul 2>nul
if not errorlevel 1 (
  python -m jupyter_mcp_tools.launcher %*
  exit /b %ERRORLEVEL%
)

echo jupyter-mcp-tools: neither a venv, nor uvx, nor an installed package was found. 1>&2
echo Run once: python %ROOT%\scripts\bootstrap.py 1>&2
exit /b 1

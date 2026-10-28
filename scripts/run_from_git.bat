@echo off
rem Zero-install launcher for the MCP server on Windows -- the exact
rem Windows equivalent of scripts/run_from_git.sh (see that file for the
rem full explanation of what and why). Meant to go straight into an MCP
rem client's own config (Claude Desktop, Claude Code, opencode, ...) as
rem the "command" to run.
rem
rem What it does, every time it's launched:
rem   1. Clone this repo into a local cache dir (or `git pull` if already
rem      cloned there).
rem   2. Use `uv` if it's already on PATH; otherwise install it with its
rem      official standalone installer (a PowerShell one-liner, not pip)
rem      and use that.
rem   3. Run `uvx --from <clone> jupyter-mcp-tools-server --transport
rem      stdio --shared-dir <clone>\shared_tools`.
rem
rem MCP client config example:
rem
rem   {
rem     "mcpServers": {
rem       "jupyter-mcp-tools": {
rem         "command": "cmd",
rem         "args": ["/c", "C:\\path\\to\\run_from_git.bat"]
rem       }
rem     }
rem   }
rem
rem Override the source/cache location or repo URL with
rem JUPYTER_MCP_TOOLS_REPO_URL / JUPYTER_MCP_TOOLS_CACHE_DIR if needed.

setlocal

if not defined JUPYTER_MCP_TOOLS_REPO_URL set "JUPYTER_MCP_TOOLS_REPO_URL=https://github.com/YakubovSlava/vyy_mcp.git"
if not defined JUPYTER_MCP_TOOLS_CACHE_DIR set "JUPYTER_MCP_TOOLS_CACHE_DIR=%USERPROFILE%\.cache\jupyter-mcp-tools-src"

set "REPO_URL=%JUPYTER_MCP_TOOLS_REPO_URL%"
set "CACHE_DIR=%JUPYTER_MCP_TOOLS_CACHE_DIR%"

if exist "%CACHE_DIR%\.git" (
  git -C "%CACHE_DIR%" pull --ff-only -q
) else (
  git clone -q "%REPO_URL%" "%CACHE_DIR%"
)

where uvx >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  set "UVX_BIN=uvx"
) else (
  powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex" >nul 2>nul
  set "UVX_BIN=%USERPROFILE%\.local\bin\uvx.exe"
)

"%UVX_BIN%" --from "%CACHE_DIR%" jupyter-mcp-tools-server --transport stdio --shared-dir "%CACHE_DIR%\shared_tools" %*

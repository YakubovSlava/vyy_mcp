# jupyter-mcp-tools

An MCP server plus a small admin web UI for running alongside JupyterLab:
administrators upload a tool as a zip archive through the UI, the tool is
installed into a shared directory, and it immediately becomes available
to every user connecting to the MCP server — no per-user setup, no server
restart.

Two processes, both part of this one package:

- **`jupyter_mcp_tools.mcp_server`** — the MCP server itself (built with the
  official `mcp` SDK's `FastMCP`). Polls the shared tools directory every
  few seconds and keeps its live tool list in sync.
- **`jupyter_mcp_tools.admin_ui`** — a FastAPI app with a one-page UI to
  upload tool archives, configure the shared directory, and remove tools.

See [`PLAN.md`](PLAN.md) for the architecture/roadmap and
[`DONE.md`](DONE.md) for a log of completed work.

## Requirements

- Python >= 3.10
- Runs on the same host as (or a host reachable by) your JupyterLab /
  JupyterHub server

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # runtime + test dependencies
pip install -e . --no-deps            # registers the console scripts below
```

Dependencies live in `requirements.txt` (runtime) /
`requirements-dev.txt` (+ `pytest`/`httpx` for tests) — `pyproject.toml`
reads its `dependencies` from `requirements.txt` too
(`[tool.setuptools.dynamic]`), so there's one list to keep in sync, not
two. Production-only install: `pip install -r requirements.txt && pip
install -e . --no-deps`.

## Bootstrap your admin account

The admin UI requires signing in — including for the main admin's own
local access — via a login page (not the browser's native Basic Auth
popup). There's no account by default, so create one before running
anything else:

```bash
jupyter-mcp-tools-admin-users add <your-username>
```

This prints a generated password (or pass `--password <...>` to set your
own). See "Main admin: issuing child-admin accounts" below for giving
accounts to other admins.

## Run the admin UI

```bash
uvicorn jupyter_mcp_tools.admin_ui.main:app --host 0.0.0.0 --port 8766
```

Open `http://<host>:8766/` directly, or — inside a JupyterHub deployment —
through `jupyter-server-proxy`'s generic port proxy at
`<jupyterhub-base>/user/<name>/proxy/8766/` (see "Open the admin UI from
JupyterLab" below). You'll land on a sign-in page — enter the username/
password you just created. From there you can:

1. Set the **shared tools directory** — a filesystem path readable by the
   MCP server process (defaults to `~/.jupyter_mcp_tools/shared_tools`).
2. **Upload a tool archive** (`.zip`) — see "Tool archive format" below.
   On success the tool is installed for every user immediately.
3. **Remove** an installed tool.

`examples/echo_tool/` has the source for a minimal example skill —
archives aren't committed to the repo, build one yourself:

```bash
python examples/build_archive.py echo_tool
```

## Open the admin UI from JupyterLab

No extra package or entry point is needed on our side —
[`jupyter-server-proxy`](https://jupyter-server-proxy.readthedocs.io/)
transparently proxies *any* locally listening port for the logged-in
user, with no registration required, **provided it's installed and
enabled in the same Python environment the Jupyter server itself runs
in** (not just any venv on the host — `pip install jupyter-server-proxy`
there, then restart the Jupyter server process so it picks up the new
server extension; `jupyter server extension list` should show
`jupyter_server_proxy ... enabled`). Without it, `/proxy/<port>/` 404s —
that 404 means the extension isn't loaded, not that the admin UI is
broken. Once it's enabled, start the admin UI as a normal background
process on the Jupyter server host:

```bash
uvicorn jupyter_mcp_tools.admin_ui.main:app --host 127.0.0.1 --port 8766 &
```

and open it from the browser at:

```
<jupyterhub-base>/user/<name>/proxy/8766
```

(the trailing slash is *not* required — `jupyter-server-proxy`'s generic
`/proxy/<port>` route doesn't always redirect to add one before serving
the page, which used to break this app's links when it assumed one. The
admin UI now reads the `X-Forwarded-Prefix` header that
`jupyter-server-proxy` sends on every proxied request and builds every
link/form action as `{that prefix}/<route>` instead of a plain relative
path, so it resolves correctly regardless of trailing slash, proxy depth,
or whether it's reached standalone on its own host:port at all.)

`jupyter-server-proxy`'s generic port proxy only forwards the request; it
doesn't add its own access control, but every route behind it now requires
the sign-in from "Bootstrap your admin account" above — anyone reaching
the URL without a valid session gets redirected to `/login`, not the UI.
There is still no check that the visiting JupyterHub user specifically is
a *Hub*
admin (see `PLAN.md` → "Open questions"); the login requirement is a
separate, app-level control, not Hub-role-aware.

## Multi-host setup: main admin and child admins

If more than one person administers tools and they aren't all on the same
machine, one host runs the real thing (the **main host** — the one with
the actual shared tools directory and MCP server) and everyone else runs a
lightweight **child-admin relay**: the same MJApp UI, but every action is
forwarded to the main host instead of touching a local directory.

### Main admin: issuing child-admin accounts

On the main host, the admin who hosts it creates one account per other
admin:

```bash
jupyter-mcp-tools-admin-users add bob
# Added admin 'bob'.
# Generated password: <random>
# Give these credentials to the admin out-of-band (they are not stored anywhere else).

jupyter-mcp-tools-admin-users list      # see who has access
jupyter-mcp-tools-admin-users remove bob  # revoke access
```

Hand the printed username/password to that admin yourself (Slack, a
password manager, whatever your org already uses) — there's no self-service
signup, and the password is never shown again after creation (reset it
with `add <username> --password <new password>` if it's lost).

### Child admin: connecting to the main host

A child admin doesn't install anything differently — they run the same
package, but launch it with `scripts/run_child_admin.sh` instead of `uvicorn`
directly:

```bash
scripts/run_child_admin.sh https://main-host.example.org:8766 bob
# Password for bob@https://main-host.example.org:8766: <enter the password the main admin gave you>
```

This starts the admin UI on `127.0.0.1:8766` (same as before — open it
through your own JupyterLab's `jupyter-server-proxy` exactly like the main
host's instance, see "Open the admin UI from JupyterLab" above), but:

- There's **no local login** for this instance — reaching it at all already
  means you're logged into your own Jupyter server, which is the trust
  boundary on this side.
- Every button in the UI (list/upload/remove skills) shells out to the
  real `curl` binary against the main host's JSON API
  (`<main-host>/api/tools`), authenticated with the username/password you
  were given. **All main↔child communication goes over `curl`** by
  design — see `common/curl_client.py` and PLAN.md — so what the relay does
  is exactly what running `curl` by hand would do, nothing hidden in a
  library.
- The "⚙" address button shows the main host's address read-only — you
  can't change where skills get installed from here. Changing the shared
  directory itself is a main-host-only action.
- If the main host uses a self-signed TLS certificate, export
  `MCP_TOOLS_INSECURE_TLS=1` before running the script (equivalent to
  `curl -k`) — only do this if you trust the network path to the main host.

## Run the MCP server

```bash
jupyter-mcp-tools-server --transport stdio
```

Point your MCP client (Claude Code, Claude Desktop, etc.) at this command.
Example `mcp.json`/client config entry:

```json
{
  "mcpServers": {
    "jupyter-shared-tools": {
      "command": "/path/to/.venv/bin/jupyter-mcp-tools-server",
      "args": ["--transport", "stdio"]
    }
  }
}
```

For a single server shared by many simultaneous users (recommended when
running alongside JupyterHub), use the HTTP transport instead so everyone
talks to one long-lived process:

```bash
jupyter-mcp-tools-server --transport streamable-http --host 0.0.0.0 --port 8765
```

Both processes read the shared directory from the same config file
(`~/.jupyter_mcp_tools/config.json` by default, override with
`MCP_TOOLS_CONFIG_PATH`), or you can force the server to a specific
directory with `--shared-dir`.

## Onboarding end users (not admins)

End users of the MCP server — as opposed to admins who manage tools —
don't get an account in `admins.json`. Instead, the main admin
distributes a single notebook file, **`notebooks/connect_to_mcp.ipynb`**,
to everyone (Slack, email, a shared drive — however you'd normally hand
someone a file).

Generate/update that file once:

```bash
python scripts/build_connect_notebook.py <main-host-url> <bitbucket-script-path>
```

- `<main-host-url>` — this admin UI's own address (used for self-registration).
- `<bitbucket-script-path>` — a script that takes a Bitbucket username as
  its argument and a password on stdin, and prints the *current* MCP host
  address to stdout. This project doesn't write that script — it's
  whatever your org already has for resolving the live address from
  Bitbucket. No real script yet? Point this at
  `scripts/mock_resolve_host.py` instead — it ignores whatever
  credentials it's given and always prints `http://127.0.0.1:8765`, so
  you can exercise the whole flow locally before the real Bitbucket
  integration exists.

Both values are baked into the notebook's first code cell as plain
`MAIN_HOST`/`BITBUCKET_SCRIPT_PATH` constants — re-run the command above,
or just hand-edit those two lines directly in the `.ipynb`, whenever
either changes.

Once distributed, every end user who opens and runs it:

- prompts for their Bitbucket username/password,
- runs `BITBUCKET_SCRIPT_PATH` locally to resolve the current host address,
- prints the MCP connection command (with their OS username filled in
  automatically via `getpass.getuser()`),
- and self-registers back to the main host over `curl` — so they show up
  in the admin UI's "Users" list right after running it, without needing
  their first real MCP call to do it.

The MCP server also logs every caller independently when run with
`--transport streamable-http`, if the client sends an `X-MCP-User` header
(the connection command above includes one) — so a user shows up even if
they connect some other way, bypassing the notebook entirely. This only
works for the HTTP transport; `stdio` has no notion of a remote caller to
log (see PLAN.md).

`POST /api/users/register` is intentionally public (no admin login, no
Basic Auth) — end users have no account to authenticate with. This is a
tracking/visibility feature, not an access control boundary; see
PLAN.md for the explicit trade-off.

## Tool archive format

A tool archive is a `.zip` with, at minimum:

```
manifest.json
<module>.py
```

`manifest.json`:

```json
{
  "name": "echo",
  "version": "1.0.0",
  "description": "Echoes the given text back",
  "entrypoint": "tool:echo",
  "parameters": { "type": "object", "properties": { "text": { "type": "string" } } }
}
```

- `name` — unique slug (`[a-z0-9][a-z0-9_-]+`), also the MCP tool name.
- `entrypoint` — `"<module>:<function>"`; `<module>.py` must exist at the
  archive root and define `<function>` as a top-level function.
- `parameters` — optional, for documentation only. The MCP input schema
  the server actually advertises is inferred from the function's type
  hints, same as any other FastMCP tool.

Validation on upload is structural only (valid zip, manifest fields,
module parses, function exists) — archive code is **not** executed during
validation, but it **is** executed when a client calls the tool. This
project assumes a trusted internal environment (the same trust boundary as
your JupyterHub/server login), not a public multi-tenant sandbox — don't
expose the admin UI to untrusted users.

### Returning multiple files for a human to run themselves

A skill's entrypoint can return any JSON-serializable value, not just a
string — FastMCP serializes it straight into the MCP response, no server
changes needed. `common/skill_results.py` standardizes the shape for a
skill that hands back **files for the calling human to save and run
under their own account**, rather than a plain text answer — the MCP
server never executes anything here, same as the Bitbucket connection
notebook:

```python
from jupyter_mcp_tools.common.skill_results import SkillFile, files_result

def make_setup_files(project_name: str) -> dict:
    return files_result(
        f"Setup files for {project_name}. Save and run these yourself.",
        [
            SkillFile("setup.sh", "#!/bin/bash\necho hi\n", run_hint="bash setup.sh"),
            SkillFile("config.yaml", f"project: {project_name}\n"),
        ],
    )
```

The response is `{"message": "...", "files": [{"filename", "content",
"run_hint"}, ...]}`. See `examples/setup_files_tool/` for a complete,
working skill using this pattern (zip it the same way as any other
example: `python examples/build_archive.py setup_files_tool`).

## Running tests

```bash
pytest
```

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
3. **Download** an installed tool back as a `.zip` — the "Скачать" link
   next to each skill. Available even in read-only mode (it's not a
   mutation). The archive is byte-for-byte what `/tools/upload` expects,
   so it can be re-uploaded as-is or shared with another instance of
   this project.
4. **Remove** an installed tool.

`examples/echo_tool/` has the source for a minimal example skill —
archives aren't committed to the repo, build one yourself:

```bash
python examples/build_archive.py echo_tool
```

### Read-only mode

Set `MCP_TOOLS_READ_ONLY=1` at startup to disable every mutating skill
action — install/overwrite, delete, and doc-skill description editing —
on that process, leaving only viewing the Skills list:

```bash
MCP_TOOLS_READ_ONLY=1 uvicorn jupyter_mcp_tools.admin_ui.main:app --host 0.0.0.0 --port 8766
```

It's a startup flag, not something toggled from the UI. The corresponding
buttons ("+", "Удалить", "Редактировать") are hidden from the page
entirely, and every mutating route (HTML and the JSON `/api/...` surface
a child relay's `curl` calls hit) rejects the action with a clear message
even if called directly. The shared-directory/git-push settings dialog
is unaffected — this only locks down skills themselves, not app config.

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

Every link/form action on every page is a plain relative path (no
`X-Forwarded-Prefix` or similar header needed) — the page redirects
itself client-side to add a trailing slash if its URL doesn't have one,
then every relative link just resolves correctly from there, regardless
of what's proxying the request (standalone, `jupyter-server-proxy`, or a
real JupyterHub whose own proxy doesn't rewrite anything for us). See
PLAN.md for the two earlier approaches that broke before landing on this
one — notably, URLs built from `X-Forwarded-Prefix` worked under
`jupyter-server-proxy` but broke under a real Hub's plainer proxy.

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

## Where data files live

Four files hold this app's own state — `admins.json`, `config.json`,
`users.json`, `session_secret` — all independent of the shared tools
directory itself (which holds the actual skill content, not metadata).
By default they live under `~/.jupyter_mcp_tools/`. To move all four at
once (e.g. to keep them outside a project checkout entirely), set one
env var before starting either process:

```bash
export MCP_TOOLS_DATA_DIR=/path/to/secrets
```

Each file also has its own specific override
(`MCP_TOOLS_ADMINS_PATH`, `MCP_TOOLS_CONFIG_PATH`, `MCP_TOOLS_USERS_PATH`,
`MCP_TOOLS_SESSION_SECRET_PATH`) that takes precedence over
`MCP_TOOLS_DATA_DIR` if you need to split them up individually. The
shared tools directory itself is unaffected by any of this — it's set
separately via the "⚙" dialog or `--shared-dir`.

## Onboarding end users (not admins)

End users of the MCP server — as opposed to admins who manage tools —
don't get an account in `admins.json`. Instead, the main admin
distributes a single notebook file, **`notebooks/connect_to_mcp.ipynb`**,
to everyone (Slack, email, a shared drive — however you'd normally hand
someone a file).

Generate/update that file once:

```bash
python scripts/build_connect_notebook.py <main-host-url> <bitbucket-base-url>
```

- `<main-host-url>` — this admin UI's own address (used for self-registration).
- `<bitbucket-base-url>` — the base URL of your org's Bitbucket Server/Data
  Center instance. The notebook's lookup cell calls that instance's REST
  API directly (`<bitbucket-base-url>/rest/api/1.0/projects/<project>/repos/<repo>/raw/<path>`,
  Basic Auth with the end user's own Bitbucket username/password or
  personal access token) to download a raw file holding the *current* MCP
  host address. Which project/repo/file that is is controlled by the
  `BITBUCKET_PROJECT`/`BITBUCKET_REPO`/`BITBUCKET_FILE_PATH` constants in
  the notebook's first cell (placeholder values by default) — hand-edit
  those three directly in the `.ipynb` to point at wherever your org
  actually keeps that file.

All of these are baked into the notebook's first code cell as plain
constants — re-run the command above, or just hand-edit the lines
directly in the `.ipynb`, whenever any of them change.

Once distributed, every end user who opens and runs it:

- prompts for their Bitbucket username/password,
- calls Bitbucket's REST API directly to resolve the current host address
  (see above — no external script to write or maintain),
- prints the MCP connection command for both Claude Code and opencode
  (with their OS username filled in automatically via
  `getpass.getuser()`),
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

A tool archive is a `.zip` holding one of two kinds of skill, detected by
which file sits at its root — a shared directory can hold a mix of both.

The archive can be either a zip *of the skill's files* (the common result
of `zip file1 file2 ...`, with `tool.py`/`SKILL.md` directly at the
root) or a zip *of a folder containing them* (the result of `zip -r
x.zip myskill/`, or a file manager's "Compress" action on a folder,
which nests everything one level deep as `myskill/tool.py`). Both work —
if every member of the archive shares one common top-level folder, it's
stripped automatically before validation, so there's no need to flatten
the folder yourself before zipping it.

### Code skills

No `manifest.json` — a code skill is a single `.py` file (any filename)
with exactly one top-level function, which is itself the manifest:

```python
# tool.py
def echo(text: str, shout: bool = False) -> str:
    """Echoes the given text back, optionally shouting it."""
    return text.upper() if shout else text
```

- The **function's name** becomes the skill's name and the MCP tool
  name — must match `[a-z0-9][a-z0-9_-]+` (any ordinary snake_case
  Python function name already does).
- The **function's docstring** becomes the skill's description —
  required, upload is rejected without one.
- The **MCP input schema** is inferred from the function's type hints,
  same as any other FastMCP tool — nothing else to declare.
- The **group** is an optional module-level `__group__ = "..."` string
  literal — see "Groups and search" below.
- The archive/folder must contain exactly one `.py` file at its root —
  more than one, or a module with more than one top-level function, is
  rejected with a clear error (there's no manifest left to say which one
  is the entrypoint).

Validation on upload is structural only (valid zip, exactly one `.py` at
the root, it parses, has exactly one top-level function, and that
function has a docstring) — archive code is **not** executed during
validation, but it **is** executed when a client calls the tool. This
project assumes a trusted internal environment (the same trust boundary as
your JupyterHub/server login), not a public multi-tenant sandbox — don't
expose the admin UI to untrusted users.

### Doc skills

A doc skill has no Python code — it's a `SKILL.md` (or `skill.md`) file at
the archive root, **plain markdown with no required syntax at all**,
plus any supporting files/folders the body refers to:

```
SKILL.md
references/architecture.md   (any supporting files the body refers to)
```

```markdown
# Repo explainer

... instructions for the agent go here ...
```

Per explicit decision, `name` and `description` are **not** read from the
file (no YAML frontmatter) — they're entered directly in the "Add a
skill" upload form instead, the same role a code skill's function
name/docstring plays:

- `name` — unique slug (`[a-z0-9][a-z0-9_-]+`), also the MCP tool name
  and the installed folder's name.
- `description` — becomes the MCP tool's own description, so a connecting
  agent can decide when to call it purely from `tools/list` without
  reading the body first.
- `group` — optional; see "Groups and search" below.

The admin-supplied description is persisted to a small
`.skill_meta.json` sidecar file inside the installed skill's folder, so
it survives being re-read from disk on every scan — there's nowhere else
left in the bundle to keep it once the frontmatter is gone.

Calling the resulting MCP tool uses progressive disclosure, not a single
dump of everything: with no arguments it returns the `SKILL.md` body plus
a listing of the skill's other files; calling it again with `file="<one
of those paths>"` returns that specific file's content. See
`examples/repo_explainer/` for a complete example and `mcp_server/server.py`'s
`_make_doc_skill_tool` for the implementation.

Once installed, an already-installed doc skill's description/group can
be edited in place — "Редактировать" on its row in the Skills tab —
without re-uploading the archive; the `SKILL.md` body and every other
file in the bundle are left untouched, only `.skill_meta.json` changes.
There's no equivalent for code skills: their description/group are the
function's own docstring and the module's `__group__`, edited by editing
the `.py` file directly.

### Groups and search

Any skill (code or doc) can be tagged with a `group` — for a code skill
via an optional module-level `__group__ = "..."` string, for a doc skill
via a form field. This always does one thing: the Skills tab gets a
search box that matches against name, description, and group together,
which matters once there are more than a handful of skills.

**Groups also need to be created on the "Группы" tab** (name +
description) to get their full effect. A skill whose `group` matches a
group that was actually created there is no longer listed as its own
tool at all — instead the whole group collapses behind one MCP tool,
named after the group, whose description *is* the group's own
description. A connecting agent calls that one tool with no arguments to
get back the list of skill names in the group (plus a short parameter
hint per skill), and calls it again with `skill=<name>,
arguments={...}` to actually run one. This is where the real context
reduction happens — the agent's `tools/list` carries one entry for the
whole group instead of one per skill in it. The tradeoff: a code skill
in a group loses its own precise, type-hinted JSON parameter schema in
favor of the group dispatcher's generic `arguments` object.

A skill whose `group` string doesn't match any group created on the
"Группы" tab (including the common case of leaving it unset) just
registers as its own standalone tool, exactly as if groups didn't exist
— nothing breaks, it simply doesn't get the hiding treatment until a
matching group is created.

A doc skill doesn't need an archive at all: "✎ Написать навык" next to
"+" on the Skills tab opens a form with **Имя**/**Описание**/a
**Markdown textarea** for the body, and creates the skill directly on
the server from that text — no zip to build and upload for the common
case of a skill with no supporting files. `registry.create_doc_skill()`
is the same validation/write path `install_tool()` uses, just skipping
the archive step; it doesn't support attaching extra files (`references/`
etc.) — upload a `.zip` for that.

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

### A skill that creates other skills

`examples/create_skill/tool.py` is a meta-skill: once installed (like any
other code skill), an agent can call it to install a **new** skill
directly into the shared directory, without the admin UI — handy for
letting an agent bootstrap its own tools on the fly.

- `create_skill(kind="doc", name=..., description=..., body=..., group="")`
  writes a new doc skill, same as "✎ Написать навык" in the admin UI.
- `create_skill(kind="code", source="<full .py file content>")` validates
  and writes a new code skill from raw source — name/description/group
  all come from the function/module in `source`, same as a code-skill
  upload.
- `overwrite=True` replaces an existing skill with the same name.

It writes straight into `load_config().shared_dir_path` — the exact
directory the running MCP server is already syncing from (`common/config.py`
is shared between the admin UI and the server process) — so the new
skill shows up in `tools/list` after the next background sync, with no
restart. This only works because this project's threat model already
trusts whatever runs inside an installed skill (no sandboxing — see
"Known limitations" in `PLAN.md`): the meta-skill imports
`jupyter_mcp_tools.common` and writes to the server's own filesystem,
which is no different from what any other installed skill could already
do.

## Automatic git push on install, delete and description edits

If the shared tools directory is **already a git repository with its
remote configured** (this project never runs `git init`/`git remote add`
for you), the main admin can set a git login/password in the "⚙"
settings dialog on the Skills page — only there, only for the main host
(a child relay has no shared directory of its own to push, see
"Multi-host setup" above). Once set, `git add -A && git commit && git
push` runs in that directory after every **install**, **delete**, and
**doc-skill description edit** (see below).

- Push is **opt-in and best-effort**: if the username/password fields
  are empty, nothing happens; if a push fails (network, auth, ...), the
  skill is still installed — the failure shows up as a warning appended
  to the success banner instead of blocking anything.
- The password is never echoed back into the settings form — leaving
  that field blank when saving other settings keeps the stored password
  unchanged. It's written to `config.json` (chmod'd `0600`, same as
  `admins.json`) alongside the other data files — see "Where data files
  live" above.
- The push itself runs attached to a real pseudo-terminal (via
  `pexpect`) and types the configured username/password in response to
  whatever "Username"/"Password" prompts git shows — the same way a
  human running `git push` by hand would. `GIT_ASKPASS`/
  `credential.helper` overrides were tried first but didn't work
  against at least one real corporate Bitbucket Server setup (some
  environments don't go through that path at all), so this is the
  fallback that's guaranteed to behave the same way a manual push does.
- A child relay's upload also triggers this: the main host pushes after
  installing, and a push failure is reported back through the relay's
  own success banner too.

## Running tests

```bash
pytest
```

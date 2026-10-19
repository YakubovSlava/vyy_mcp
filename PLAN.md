# Plan

## Goal

An MCP server for use alongside JupyterLab where an administrator can
upload a tool from a zip archive through a web UI, and that tool becomes
available to every user of the server — without restarting anything.

## Architecture decisions

- **Dependencies live in `requirements.txt`/`requirements-dev.txt`, not
  duplicated in `pyproject.toml`.** Per explicit request. `pyproject.toml`
  declares `dynamic = ["dependencies"]` and reads them from
  `requirements.txt` via `[tool.setuptools.dynamic]`, so there's one list
  to edit, not two that can drift. Install is therefore two steps instead
  of one (`pip install -r requirements-dev.txt` then
  `pip install -e . --no-deps`) — the `--no-deps` on the second step
  matters: without it, pip would re-resolve dependencies from
  `pyproject.toml` (which just points back at `requirements.txt` anyway,
  but there's no reason to make pip do that resolution twice).
- **Two independent processes, one package.** The MCP server
  (`mcp_server/`) and the admin UI (`admin_ui/`) are separate FastAPI/MCP
  apps that only share filesystem state (the shared tools directory) and a
  small JSON config file. This was chosen over a single combined process
  so the MCP server's lifecycle (used by MCP clients) isn't coupled to the
  admin UI's lifecycle (used by humans in a browser), and so the admin UI
  can be restarted/updated without disconnecting active MCP sessions.
- **Filesystem as the shared store**, not a database. The admin's choice
  of shared directory doubles as the distribution mechanism: any process
  with read access to that path (e.g. every JupyterHub single-user server
  on a shared filesystem) can see installed tools. Simpler to operate than
  standing up Postgres/SQLite for what is fundamentally "a folder of
  scripts," per the user's explicit preference.
- **Polling, not file watching.** The MCP server re-scans the shared
  directory every 5 seconds (`SYNC_INTERVAL_SECONDS` in `server.py`)
  instead of using `watchdog` or inotify. Avoids an extra dependency and
  cross-platform file-event quirks; 5s latency between "admin clicks
  install" and "tool is callable" is an acceptable trade-off for this use
  case.
- **`mcp` SDK pinned to `<2`.** The SDK's 2.x line renamed `FastMCP` to
  `MCPServer` and changed the handler-registration API (constructor
  `on_list_tools=`/`on_call_tool=` callbacks instead of decorators). The
  1.x `FastMCP` decorator/`add_tool`/`remove_tool` API is what most
  existing MCP client docs and tutorials (and the user's own "fastmcp"
  framing) assume, so the project targets that for now. Revisit this pin
  once the 2.x ecosystem settles (see Open questions).
- **Input schema comes from Python type hints, not `manifest.json`.**
  FastMCP's `add_tool` infers the JSON schema straight from the
  entrypoint function's signature (pydantic-style), which is more accurate
  than hand-written JSON Schema and avoids keeping two schemas in sync.
  `manifest.json`'s `parameters` field is kept for human-readable
  documentation only.
- **Admin UI is reached through `jupyter-server-proxy`'s *generic* port
  proxy** (`.../user/<name>/proxy/<port>/`), not a named
  `jupyter_serverproxy_servers` entry point. We deliberately don't
  register one: the generic proxy needs zero registration/config on our
  side (it proxies any port the user's process binds to), and that's the
  access pattern the actual deployment uses — no Launcher card, just
  `uvicorn admin_ui.main:app --port <port>` run as a normal background
  process and opened at that URL.
- **Every link/form action is relative (no leading `/`), with a
  client-side trailing-slash self-correct script — no proxy headers, no
  server-side URL rewriting assumptions.** This went through two prior,
  each eventually-broken designs; worth recording why both failed, since
  it's tempting to reach for either again:
  1. *Plain relative paths, no safety net.* `jupyter-server-proxy`'s
     generic `/proxy/<port>` route doesn't reliably redirect to add a
     trailing slash before serving the page, so the browser resolved
     `tools/upload` one directory too high
     (`.../proxy/config/shared-dir` instead of
     `.../proxy/8766/config/shared-dir`), which Jupyter's own Tornado
     layer rejected with a misleading `403 '_xsrf' argument missing from
     POST` (the error looks like it's about this app; it's actually
     Jupyter rejecting what looks like a cross-site POST to one of its
     own routes). Root-caused via the real Jupyter server's log.
  2. *Absolute paths built from the `X-Forwarded-Prefix` header.*
     `jupyter-server-proxy` sends that header and even rewrites
     `Location` on redirects to match — so this worked under
     `jupyter-server-proxy`, including from a browser. It broke on a
     **real JupyterHub** deployment: JupyterHub's own front-door proxy
     (`configurable-http-proxy`) is a dumb passthrough that does
     **not** set `X-Forwarded-Prefix` or rewrite `Location` headers the
     way `jupyter-server-proxy`'s own handler does. With no header,
     `base` fell back to `""`, so `{{ base }}/<route>` collapsed to a
     plain absolute path (`/tools/upload`) — which resolves against the
     *domain* root in the browser, escaping the Hub's
     `/user/<name>/...` prefix entirely and landing on Jupyter's own
     default route (observed as a `GET /` 303 in the Jupyter log,
     eventually surfacing as a redirect to `/lab/workspaces/...`).
  3. **Current fix**, confirmed against a known-working sibling project
     (`vyy_trees`, deployed on the exact same Hub) using the identical
     pattern: every template link/form `action` is a plain relative
     path with no leading `/` and no computed prefix at all (e.g.
     `action="tools/upload"`, `href="users"`, `href="."` for the root).
     This only resolves correctly if the *current* page's URL ends in
     `/`, which isn't guaranteed by every proxy — so a tiny inline
     `<script>` at the top of `<head>` in `base.html`/`login.html`
     redirects client-side to add the trailing slash before anything
     else loads (`location.replace(location.pathname + "/")`). No
     server has to rewrite anything, no header has to be forwarded
     correctly — it works identically standalone, under
     `jupyter-server-proxy`, or under a real Hub's plain proxy.
- **All four data files (`admins.json`, `config.json`, `users.json`,
  `session_secret`) share one base directory via `common/paths.py`'s
  `data_dir()`** (env var `MCP_TOOLS_DATA_DIR`, default
  `~/.jupyter_mcp_tools`), rather than each independently defaulting to
  the same hardcoded path. Per explicit request: the admin wanted these
  moved out from under the project checkout entirely (in this
  deployment, to the parent of the repo directory) without juggling four
  separate env vars. Each file's existing specific override
  (`MCP_TOOLS_ADMINS_PATH`, etc.) still takes precedence over
  `MCP_TOOLS_DATA_DIR` if set — this only changes what they fall back to.
  The shared **tools** directory (`shared_tools/`, the actual skill
  content, configured via the "⚙" dialog / `--shared-dir`) deliberately
  stays independent of `data_dir()` — it's content admins manage through
  the UI, not app secrets, and moving it wasn't part of the request.
- **Flat, equally-privileged admin accounts**, checked against
  `common/auth.py`'s store (`~/.jupyter_mcp_tools/admins.json`,
  PBKDF2-HMAC-SHA256 hashes — stdlib `hashlib` only, no bcrypt/passlib
  dependency, consistent with this project's "basic validation, trusted
  operators" level elsewhere). There is one flat set of admins with
  identical privileges; the main admin issues accounts via
  `jupyter-mcp-tools-admin-users` (a separate CLI, not a UI feature, since
  account management needs filesystem access to `admins.json` that a
  remote child admin shouldn't have). No self-registration, no password
  reset flow beyond re-running `add` with `--password`, no roles/
  permission tiers — any admin can do anything any other admin can.
- **Two different checks against the same account store, for two
  different callers.** First version gated every route (including the
  browser) with HTTP Basic Auth — simple, but the browser's native
  username/password popup looks jarring next to a styled app and can't be
  restyled. Switched the *browser-facing* HTML routes
  (`/`, `/tools/upload`, `/tools/{name}/delete`, `/config/shared-dir`) to
  a normal HTML login form (`/login`) plus a signed, expiring session
  cookie (`common/session.py` — HMAC-SHA256 over `username:expiry` with a
  random per-install secret key on disk, 12h TTL; no new dependency, no
  `itsdangerous`/`SessionMiddleware`). The JSON API
  (`/api/tools`, `/api/tools/{name}/delete`) *keeps* HTTP Basic Auth,
  since that's what a child relay's `curl` calls authenticate with — a
  `curl -u user:pass` call has no browser to show a login page to.
  `require_admin_page` (cookie, raises `NotAuthenticated` → redirects to
  `/login`) and `require_admin_api` (Basic Auth, raises 401) are
  deliberately separate FastAPI dependencies against the same
  `common/auth.py` store — a valid session cookie does **not** grant API
  access and vice versa.
- **Redirect `Location` headers are built by `_relative_redirect()`
  (`admin_ui/main.py`), which climbs the right number of `../` based on
  the *current* request path's depth** (`request.url.path`), rather
  than assuming any particular depth or relying on a proxy to fix up
  the header. E.g. redirecting-to-login from `POST /tools/upload`
  (2 segments deep) emits `Location: ../login`; from the root `GET /`
  it emits plain `login`. This is the redirect-side counterpart of the
  relative-body-link decision above, for the same reason: no proxy in
  this project's supported deployments (standalone,
  `jupyter-server-proxy`, real JupyterHub) can be assumed to rewrite
  `Location` headers, so the app has to emit an already-correct
  relative reference itself. Verified directly against the running
  admin UI with `curl` at multiple route depths (root, `/tools/upload`,
  `/tools/{name}/delete`, `/config/shared-dir`), not just by reasoning
  about RFC 3986.
- **End users are tracked, not authenticated.** Per explicit decision:
  regular MCP users (as opposed to admins) get no account in
  `admins.json` and no password of their own. `common/users.py` is a
  flat JSON store (`users.json`) of "who's shown up," populated by a
  single `record_user(username)` upsert, called from two independent
  places: the distributed connection notebook's self-registration
  (`POST /api/users/register`) and the MCP server itself, which upserts
  by username on every call it serves when run over `streamable-http`
  (see `LogMCPUserMiddleware` in `mcp_server/server.py`) — covering users
  who connect without ever running the notebook. `POST /api/users/register`
  is deliberately public (no login): an end user has nothing to
  authenticate *with*. This is a visibility feature for the main admin,
  not an access-control boundary — anyone who can reach the main host can
  self-register an arbitrary username, same trust level as the rest of
  this project's "internal network" threat model.
- **One static, distributed notebook file, not a per-user generated
  download.** First version generated a personalized `.ipynb` per invite
  code behind `GET /connect/<code>`, with a "Invite a user" button in the
  UI creating that code — dropped per explicit decision in favor of a
  single file, `notebooks/connect_to_mcp.ipynb`, that the main admin
  distributes however they'd distribute any file (Slack, a shared drive),
  identical for every recipient. `scripts/build_connect_notebook.py`
  (thin wrapper around `admin_ui/notebook_builder.build_notebook_json()`)
  bakes `MAIN_HOST`/`BITBUCKET_SCRIPT_PATH` in as plain constants in the
  notebook's first code cell — re-run the script, or hand-edit those two
  lines directly in the committed `.ipynb`, whenever either changes. This
  is simpler to operate (no web endpoint serving dynamic content to
  unauthenticated requests, no code-guessing surface) at the cost of no
  per-recipient tracking of *who was sent* a link — only who's actually
  run it or called the MCP server shows up in `users.json`.
- **The Bitbucket host-lookup script is a pluggable, unwritten
  dependency**, not something this project implements. The notebook
  exists specifically so the main host's address never has to be
  hardcoded anywhere a user might copy-paste a stale value from: at
  connect time, it invokes `BITBUCKET_SCRIPT_PATH` (baked in at
  generation time from `scripts/build_connect_notebook.py`'s second
  argument) — a script, provided by whoever manages the org's Bitbucket
  credentials/API, that takes a username (argv) and password (stdin) and
  prints the current address to stdout. We only define and document the
  calling convention; we don't ship or validate that script's contents
  (unlike tool archives, there's no `ast`-based structural check here —
  it's an admin-trusted external dependency, not user-uploaded content).
  `scripts/mock_resolve_host.py` is a stand-in that ignores credentials
  and always returns `http://127.0.0.1:8765`, for exercising the flow
  before the real script exists.
- **MCP user logging only works for `streamable-http`, not `stdio`.**
  `stdio` transport has no network identity to log in the first place —
  it's a local subprocess the MCP client spawns itself, so "which remote
  user called this" isn't a meaningful question there. For
  `streamable-http`, `LogMCPUserMiddleware` reads a plain `X-MCP-User`
  header (no signature, no verification — same tracking-not-auth caveat
  as above) and upserts into the same `users.json` the admin UI reads.
  Implementing this required bypassing `FastMCP.run()` (it has no hook to
  attach Starlette middleware) and instead building the ASGI app via
  `app.streamable_http_app()`, attaching the middleware, and serving it
  with `uvicorn.run()` directly — see `main()` in `mcp_server/server.py`.
- **Main↔child communication goes over the real `curl` binary, by
  explicit requirement**, not a Python HTTP client library. A child admin
  doesn't have a local shared tools directory; their instance of the same
  admin UI (`scripts/run_child_admin.sh`) is configured with
  `MCP_TOOLS_MAIN_HOST` and forwards every list/upload/delete action to
  the main host's JSON API (`/api/tools`, `/api/tools/{name}/delete`) by
  shelling out via `common/curl_client.py`. The practical effect: what the
  relay does to the main host is exactly what a human typing the
  equivalent `curl -u user:pass ...` command would do — nothing happens
  over a code path a human couldn't replicate and inspect by hand. The
  main host's own browser-facing HTML routes still call the local
  `registry`/`config` modules directly (no reason to route a process's own
  local filesystem access through its own HTTP API); only the *child*
  side is curl-based.
- **A separate JSON API surface (`/api/tools`, `/api/tools/{name}/delete`)
  exists alongside the HTML routes**, rather than having the child relay
  scrape the human-facing HTML. The HTML routes return 200 with an error
  *banner in the body* on failure (so the browser always gets a renderable
  page); machine callers need real status codes (400 validation, 409
  duplicate, 404 missing) and structured JSON instead, so they're separate
  endpoints rather than a content-negotiation branch on the same route.
- **Validation is structural, not sandboxed.** Per explicit decision: the
  admin UI parses the archive with `zipfile`/`ast` and never executes
  uploaded code during validation. Execution happens only when an MCP
  client calls the tool, same trust level as any other code an admin can
  already run on the server. No seccomp/container sandboxing — this
  targets a trusted-admin, internal-network deployment (same boundary as
  JupyterHub auth), not a public multi-tenant platform.
- **Multi-file skill results needed zero MCP-server changes** — only a
  documented convention. A skill's entrypoint can already return any
  JSON-serializable value (not just `str`), and FastMCP serializes it
  straight into the MCP response; verified this directly against the
  *running* server (installed `examples/setup_files_tool`, called it
  through a real `mcp` SDK client session, got back structured JSON)
  rather than assuming it from docs. `common/skill_results.py`'s
  `files_result()`/`SkillFile` just standardize the shape
  (`{"message", "files": [{"filename", "content", "run_hint"}]}`) so
  every skill that hands back files for a human to run themselves looks
  the same to whoever's driving the MCP client — the server never
  executes any of it, same posture as the Bitbucket connection notebook.
  Note: `call_tool()`'s return shape itself differs by return type in
  this `mcp` SDK version — a `str`-returning tool yields
  `(content_list, structured_dict)`, a `dict`-returning one yields just
  `content_list` — `test_multi_file_skill_result_round_trip` in
  `test_server_sync.py` handles both rather than assuming one.

## Shared tools directory layout

```
<shared_dir>/
  tools/
    <name>/
      manifest.json
      <module>.py
      ... (any other files from the archive)
```

## Known limitations / explicitly out of scope

- No sandboxing of tool execution (see above).
- The main host's admin UI now requires an app-level login (session
  cookie via `/login` for the browser, Basic Auth for the JSON API — see
  "Architecture decisions" above), but it is **not aware of
  JupyterHub roles** — it has no idea whether the authenticated user is a
  Hub admin, a regular Hub user, or not a Hub user at all. Anyone who
  knows (or is given) a valid username/password from `admins.json` has
  full admin rights, regardless of their JupyterHub identity. Deploy
  behind the same network boundary as JupyterHub and treat the two auth
  layers (Hub login to reach the proxy at all, then this app's Basic Auth)
  as independent, not a finished single permission model.
- No per-admin audit trail — the main host doesn't currently log *which*
  admin account performed a given upload/delete, only that *some* valid
  credential did.
- No tool versioning/rollback — installing with `overwrite=True` replaces
  the previous version in place; there's no history.
- Single shared directory per MCP server instance; no per-user or
  per-group tool visibility.
- `/api/users/register` is unauthenticated by design (see "Architecture
  decisions" → end users). Anyone who can reach the main host can
  self-register an arbitrary `username` into `users.json` — no rate
  limiting. This is a display feature, not an access gate — it doesn't
  grant any tool access by itself, since the MCP server itself has no
  per-user authorization, only per-user logging.
- Distributing `notebooks/connect_to_mcp.ipynb` means **anyone with the
  file** can see `MAIN_HOST` and the local path to the Bitbucket script —
  neither is secret by itself, but it does mean the file shouldn't be
  posted somewhere wholly public if `MAIN_HOST` isn't meant to be.
- `X-MCP-User` is a plain, unverified header — any MCP client can claim
  to be any username. The "Users" list reflects *what clients say*, not a
  verified identity.
- Bitbucket credentials entered in the connection notebook are only as
  safe as the notebook's own execution environment (the end user's
  JupyterLab) and the configured lookup script; this project has no
  visibility into or control over either.

## Open questions / future work

- **Tie app-level accounts to JupyterHub identity.** Current state: this
  app's Basic Auth (see "Architecture decisions") is a separate credential
  from JupyterHub's own login — a child admin's username/password here
  has no relationship to their Hub username. Options discussed: (a) check
  the `X-JupyterHub-User` header the Hub sets on proxied requests against
  the Hub's Admin API (`GET /hub/api/users/{name}`, `admin: true`) using a
  hub service token, and auto-derive the admin identity from that instead
  of a separate `admins.json` account; (b) keep the two separate (current
  state) but add a check that `X-JupyterHub-User` matches the Basic Auth
  username as a sanity check. Not yet decided.
- **Per-admin audit logging** of who uploaded/removed which tool —
  `admins.json`'s PBKDF2 check happens inside `require_admin` in
  `admin_ui/main.py`, which already knows the authenticated username; it's
  just not logged or attached to any action yet.
- **Password rotation/expiry.** `jupyter-mcp-tools-admin-users add
  <user> --password <new>` overwrites in place, but there's no forced
  rotation, expiry, or notification if a child admin's credential is
  suspected compromised beyond the main admin manually running `remove`
  then `add` again.
- Decide whether to track the `mcp>=2` (`MCPServer`) API and migrate once
  it's clearly the ecosystem default, or stay on 1.x long-term.
- Consider a "staging" step in the admin UI (preview manifest before
  install) if uploads become frequent enough that overwrite mistakes are
  costly.
- Consider per-tool enable/disable toggle without full removal.
- If latency from the 5s poll becomes a problem, add a manual
  "notify MCP server" webhook call from the admin UI's upload/delete
  handlers instead of (or in addition to) polling.
- **No per-recipient tracking of who was *sent* the notebook** — since
  it's one static file handed out by whatever means (Slack, a shared
  drive), there's no record of who received it, only of who's actually
  run it or called the MCP server. If that gap matters, the dropped
  invite-code approach (see "Architecture decisions" above) is the
  fallback design to revisit.
- **No way to revoke a tracked end user** from the UI — `users.json` only
  grows. Since it's a display feature rather than an access gate, this is
  lower priority than it would be for a real permission system, but a
  "remove" button would still be useful for keeping the list meaningful.
- Consider actually verifying `X-MCP-User` (e.g. signing it the same way
  `common/session.py` signs admin sessions) if the "Users" list ever
  needs to be trustworthy rather than just informative.

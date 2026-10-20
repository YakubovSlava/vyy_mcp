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
- **Input schema comes from Python type hints, not a manifest file.**
  FastMCP's `add_tool` infers the JSON schema straight from the
  entrypoint function's signature (pydantic-style), which is more accurate
  than hand-written JSON Schema and avoids keeping two schemas in sync.
  This observation is also what made dropping `manifest.json` entirely
  possible later (see the "code skills need no manifest.json" entry
  below) — the schema was never actually read from it.
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
     `<script>` redirects client-side to add the trailing slash before
     anything else loads (`location.replace(location.pathname + "/")`).
     No server has to rewrite anything, no header has to be forwarded
     correctly — it works identically standalone, under
     `jupyter-server-proxy`, or under a real Hub's plain proxy.
  4. **One more depth-specific wrinkle this missed initially**: the
     self-correct script only matters for the *app root* (`/`). Every
     other route (`/users`, `/docs`, `/login`, `/tools/upload`, ...) has
     a real trailing path segment of its own, so relative links off of
     it resolve unambiguously regardless of whether that page's own URL
     ends in `/` — but adding a trailing slash to one of *those* (which
     a blindly-copied self-correct script would do) actually breaks it,
     since this app has no route registered for e.g. `/login/` (404).
     `vyy_trees` doesn't hit this because it only has one such leaf page
     (`tree.html`) and deliberately doesn't put the script there (only
     on `index.html`) — easy to miss copying that distinction over.
     Here: the script lives in a Jinja block (`self_correct_script` in
     `base.html`) that only `index.html` overrides; `login.html` only
     includes it when rendered as the root's unauthenticated fallback
     (`self_correct=True`, set only by `_redirect_to_login` when
     `request.url.path` is literally `/` — every other
     `NotAuthenticated` case keeps using the depth-computed
     `_relative_redirect("login")`, which is correct for those since
     they're always reached via relative navigation from an
     already slash-corrected root, so their *own* backend-visible depth
     matches the browser's).
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
- **Starlette's own `redirect_slashes` had to be disabled** — it was a
  completely separate hole from the one `_relative_redirect()` above
  closes, discovered from a real production report (a proxied upload
  redirecting straight to the domain root, dropping the whole
  `/user/<name>/proxy/<port>` prefix). Reproduced locally: `POST
  /tools/upload/` (trailing slash) 307-redirects, by Starlette's own
  built-in behavior, to `http://testserver/tools/upload` — an
  **absolute** Location (scheme+host+path) built from
  `request.url.replace(...)`, with zero awareness of any proxy prefix.
  Every one of our own redirects already goes through
  `_relative_redirect()`, but this one is emitted by the framework
  itself before our routing even runs, so that discipline never applied
  to it. Fixed by `FastAPI(..., redirect_slashes=False)` plus a small
  `StripTrailingSlashMiddleware` that strips a trailing slash from
  `scope["path"]` before routing — so a proxy that happens to forward
  one (we don't know which of theirs does, or why) hits the route
  directly instead of either 404ing or triggering any redirect at all.
  `test_trailing_slash_never_triggers_a_redirect` reproduces the exact
  shape of the original report (`POST /tools/upload/`, `GET /users/`,
  `GET /docs/`) and asserts 200, not 307.
- **Mutating POSTs (`/tools/upload`, `/tools/{name}/delete`,
  `/config/shared-dir`) now redirect (303) back to the Skills page
  (`.`) with the outcome passed as a `message`/`error` query param,
  reusing the already-proven `_relative_redirect()` helper** — reversing
  an earlier decision (see the 2026-10-19 "page split" PLAN entry) to
  render `index.html` directly at the POST's own URL with no redirect
  at all, specifically to avoid an *absolute* redirect escaping the
  proxy prefix. That reasoning no longer applies: `_relative_redirect()`
  (used by `/login` for exactly this "bounce back after a successful
  POST" case already) was proven correct under a real proxy well after
  that original decision was made, so there's no remaining reason to
  leave the address bar sitting on `/tools/upload` or
  `/tools/{name}/delete` instead of back on the actual list — which is
  what users actually expect ("редиректит на основную после удаления и
  добавления" — explicit request). This also fully eliminates the
  self-correct-script-fires-on-a-POST-response class of bug just above:
  `_render()` (and therefore `index.html`'s self-correct script) is now
  *only* ever reached via `GET /`, so `self_correct` is unconditionally
  true there and the guard is a documented invariant rather than a
  tested edge case. `index()` now accepts optional `message`/`error`
  query params read straight into the same banner the inline-render
  used to pass directly.
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
- **The Bitbucket host lookup calls Bitbucket's REST API directly from
  the notebook, not an external pluggable script.** Originally this was
  a deliberately unwritten, pluggable dependency (`BITBUCKET_SCRIPT_PATH`,
  an admin-provided script taking a username/password and printing the
  address) — changed per explicit request ("сделай так, чтобы код я прям
  вставил по извлечению данных с bitbucket": give real code to paste in,
  not a placeholder). `notebook_builder.py`'s lookup cell now does a
  real `requests.get(...)` against a Bitbucket Server/Data Center REST
  endpoint (`<base>/rest/api/1.0/projects/<project>/repos/<repo>/raw/<path>`)
  with Basic Auth, assuming self-hosted Bitbucket Server/Data Center
  (not Bitbucket Cloud, whose API shape differs) since that's the far
  more common setup behind an internal JupyterHub. `BITBUCKET_PROJECT`/
  `BITBUCKET_REPO`/`BITBUCKET_FILE_PATH` are baked in with placeholder
  values (`"PROJ"`/`"mcp-config"`/`"host.txt"`) the admin hand-edits
  directly in the notebook to point at wherever the org actually keeps
  the current host address — same "bake in constants, hand-edit if they
  change" pattern `MAIN_HOST` already used. `AppConfig.bitbucket_base_url`
  (renamed from `bitbucket_script_path`) now only holds the Bitbucket
  instance's base URL, shown in the admin UI purely as a reminder of
  what's baked into the distributed notebook — it isn't read at request
  time by anything. `scripts/mock_resolve_host.py` (the old script-based
  flow's local stand-in) was deleted; there's no script to stand in for
  anymore.
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
  scrape the human-facing HTML. The HTML routes always end up rendering
  the Skills page with an error/success *banner in the body* (so the
  browser always gets a renderable page — see the redirect-to-root entry
  below for exactly how that page is reached since 2026-10-20); machine
  callers need real status codes (400 validation, 409 duplicate, 404
  missing) and structured JSON instead, so they're separate endpoints
  rather than a content-negotiation branch on the same route.
- **Validation is structural, not sandboxed.** Per explicit decision: the
  admin UI parses the archive with `zipfile`/`ast` and never executes
  uploaded code during validation. Execution happens only when an MCP
  client calls the tool, same trust level as any other code an admin can
  already run on the server. No seccomp/container sandboxing — this
  targets a trusted-admin, internal-network deployment (same boundary as
  JupyterHub auth), not a public multi-tenant platform.
- **`FastMCP`'s `host` must be passed in at construction, not set on
  `app.settings` afterwards.** `mcp_server/server.py`'s `build_app()`
  was constructing `FastMCP(...)` with no `host` kwarg (defaulting to
  `"127.0.0.1"`), then `main()` set `app.settings.host = args.host`
  after the fact when binding `0.0.0.0` for `streamable-http`. That
  looked harmless but wasn't: `FastMCP.__init__` auto-enables DNS-
  rebinding protection — locking `Host`-header validation to
  `127.0.0.1`/`localhost`/`::1` patterns — *whenever it sees one of
  those as `host` at construction time*, and that decision, baked into
  `self.settings.transport_security`, doesn't get revisited just
  because `app.settings.host` changes later. Every request through a
  real proxy (JupyterHub, opencode connecting to a public domain, ...)
  sends a `Host` header that isn't `127.0.0.1`-shaped, so every one of
  them got rejected with `421 Misdirected Request` — a response that
  comes from `mcp.server.transport_security`, not from this app's own
  code, which made it look unrelated to anything we'd written. Root-
  caused by reading that module's source (`_validate_host` /
  `Response("Invalid Host header", status_code=421)`), not by guessing
  from the error text. Fixed by passing `host=`/`port=` straight into
  `build_app()`'s own `FastMCP(...)` call instead of `main()` patching
  `app.settings` post-hoc — binding `0.0.0.0` now means FastMCP's
  `host in ("127.0.0.1", "localhost", "::1")` check is false, so it
  doesn't auto-enable the host-locking at all (same trust-boundary
  reasoning as the rest of this project: an internal/proxied deployment
  isn't the public-rebinding-attack scenario this protection targets).
  Verified against the *running* server with a real `mcp` SDK client
  session carrying a foreign `Host` header, not just by reasoning about
  the SDK's source.
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

- **Doc skills are a second bundle kind, detected by presence rather than
  an explicit "kind" field.** A skill folder/archive is either a code
  skill (a single `.py` file at its root) or a doc skill (`SKILL.md`/
  `skill.md` at its root) — `registry._validate_archive`/`_load_installed`
  branch on which exists rather than requiring an upload to declare its
  kind up front. Chosen because Claude's "Agent Skills" convention
  already uses `SKILL.md`'s mere presence as the signal. `common/doc_skill.py`'s
  `DocSkillManifest` and `common/manifest.py`'s `Manifest` both expose a
  `.kind` property (`"doc"`/`"code"`) purely for the admin UI to label
  them with; it isn't used anywhere in the validation/dispatch logic
  itself, which stays presence-based throughout (`registry.AnyManifest =
  Manifest | DocSkillManifest`).
- **Code skills need no `manifest.json` at all — the function is its own
  manifest.** Per explicit request ("Модуль с документацией выглядит
  некрасиво. Сделай так, чтобы нам не нужны были manifest json" — the
  separate JSON file was ugly/redundant ceremony around a single
  function). `common/manifest.py` now derives everything a `manifest.json`
  used to declare straight from the single `.py` file's AST: the
  function's **name** (must match the same `NAME_RE` as before — an
  ordinary snake_case name already does), its **docstring** (required —
  becomes `description`), and an optional module-level `__version__ =
  "..."` string literal (defaults to `"1.0.0"`). `entrypoint` is computed
  as `f"{module_stem}:{func.name}"` instead of being declared explicitly.
  The archive/folder must contain exactly one top-level `.py` file with
  exactly one top-level function — both now validated with a clear error
  message, since there's no manifest left to disambiguate which file/
  function was meant. The `parameters` field is simply gone: it was
  already documentation-only dead weight (see "Input schema comes from
  Python type hints" above) that nothing ever read. No backwards
  compatibility shim for old `manifest.json`-based archives was added —
  per this project's conventions, and because every existing example
  (`echo_tool`, `setup_files_tool`) already had a docstring on its single
  function, so migrating them was just deleting the now-redundant
  `manifest.json` file.
- **A doc skill's `description` becomes the MCP tool's own
  `description`**, not just documentation. This is the entire point of
  the feature per explicit request — "на уровне инструмента должно
  задаваться описание, чтобы агент понимал, когда нужно обращаться к
  конкретному скиллу" (the tool-level description must let an agent know
  when to reach for this specific skill). `mcp_server/server.py`'s
  `sync_tools()` passes `manifest.description` into `FastMCP.add_tool`
  for doc skills the same way a code skill's docstring/description flows
  through today, so a connecting agent can decide whether to invoke the
  skill purely from `tools/list`, without reading `SKILL.md`'s body first.
- **`SKILL.md` carries no YAML frontmatter at all — `name`/`description`
  are entered directly in the admin UI's upload form instead.** Reversed
  the original design (frontmatter-based, mirroring Claude's Agent
  Skills format exactly) per explicit follow-up request: "никто так не
  пишет" — writing a YAML block by hand in a markdown file is unnatural
  ceremony nobody actually does, and the project had just made the
  *code*-skill case admin/author-friendly by deriving its manifest from
  the function itself (see above) — doc skills needed the same kind of
  simplification, just via a form field instead of a parsed file, since
  there's no code to introspect. Explicitly chose "drop frontmatter
  entirely, always from the form" over "keep it as an optional
  override" (asked via AskUserQuestion) to avoid the ambiguity of two
  sources of truth disagreeing. `common/doc_skill.py`'s
  `validate_doc_skill_archive()` now takes `name`/`description` as
  required keyword arguments instead of parsing them out of the file;
  `SKILL.md` is just `.strip()`-ed and used as the body verbatim.
  Dropped the `pyyaml` dependency entirely along with the parsing code —
  nothing in this project needs YAML for anything else.
- **The admin-supplied description has nowhere left in the bundle to
  live, so it's persisted to a `.skill_meta.json` sidecar file written
  at install time** (`common/doc_skill.py`'s `write_meta()`), read back
  by `validate_doc_skill_dir()` on every re-scan (`registry.list_tools`
  re-derives every manifest from disk on every call, same as a code
  skill's docstring — there's no cached manifest file for either kind).
  `name` doesn't need its own sidecar entry: it's already the installed
  folder's name (`validate_doc_skill_dir` uses `skill_dir.name` instead
  of a stored field), the same way `tools_dir(shared_dir) / manifest.name`
  already worked for every other installed skill. The sidecar file is
  excluded from the doc skill's own `files` listing (what an agent sees
  offered via `file="<path>"`) the same way `SKILL.md` itself already
  was.
- **Both upload paths (the HTML form and the JSON `/api/tools` a child
  relay's `curl` call hits) gained `name`/`description` form fields**,
  always present but only meaningful for a doc-skill upload — a code
  skill ignores them entirely (its name/description always come from the
  function). `admin_ui/main.py`'s `upload_tool()`/`api_upload_tool()`
  both forward them straight into `registry.install_tool(..., name=,
  description=)`; the child-relay branch adds them to the `curl_client.post`
  `fields=` dict alongside `overwrite`, so a relay's forward to the main
  host carries them too.
- **Doc skills use progressive disclosure, not a single bulk response.**
  Asked explicitly (file content on every call vs. SKILL.md + listing
  first, files on request) — chose the latter. A no-argument call to a
  doc skill's tool (`_make_doc_skill_tool` in `mcp_server/server.py`)
  returns the `SKILL.md` body plus a plain listing of the skill's other
  files; a follow-up call with `file="<path>"` returns that one file's
  text content, or an error listing the valid paths if it doesn't match.
  Keeps the typical case (an agent just needs the main instructions)
  cheap, while still letting an agent pull in `references/*.md` or
  similar supporting files only when the task actually needs them —
  mirrors how Claude's own Agent Skills are meant to be consumed.
- **`get_tool()` was removed from `registry.py`** while reworking it to
  support both bundle kinds — grepped the codebase first and confirmed no
  production code or test referenced it; keeping an unused accessor
  "just in case" isn't this project's convention (see top-level
  guidelines). `list_tools()`/`install_tool()`/`manifest_to_dict()` are
  the only entry points the admin UI and MCP server actually use.
- **`dataclasses.asdict()` silently drops `@property` fields** — found
  while adding `.kind` to both manifest types: `asdict()` only serializes
  real dataclass fields, so `manifest_to_dict()` has to merge `kind` in
  explicitly (`{**asdict(manifest), "kind": manifest.kind}`) rather than
  relying on `asdict()` alone, or the admin UI's code/doc badge would
  silently always be missing that key.
- **Automatic `git push` of the shared directory after every skill
  install, per explicit request.** Scoped deliberately narrow based on
  the user's own answers: the shared directory is assumed to *already*
  be a git repo with its remote configured — this project never runs
  `git init`/`git remote add` itself, only `add`/`commit`/`push` in an
  existing repo; and auth is plain HTTPS username/password (not a
  token-specific flow, not SSH), matching what the user actually has.
  New `common/git_push.py` shells out to the real `git` binary via
  `subprocess` (same "it's exactly what a human typing the command would
  do" philosophy as `common/curl_client.py`'s curl-only rule for
  main↔child traffic, applied here to git for the same reason —
  auditability over a wrapper library). Push is best-effort: a failure
  never blocks the install that triggered it, only appends a warning to
  the success banner (both the direct HTML upload path and the JSON
  `/api/tools` a child relay's `curl` call hits — the latter via a new
  non-breaking `git_push_error` field in that endpoint's response,
  which the relay's own HTML handler reads back out and appends to its
  own banner).
- **Credentials for `git push` go through `GIT_ASKPASS`
  (`scripts/git_askpass.py`), never embedded in the push command/URL
  itself.** `ps aux` on a shared host shows every process's argv to
  every local user; a plaintext password baked into the git command
  line would leak there. The askpass script instead reads
  `GIT_PUSH_USERNAME`/`GIT_PUSH_PASSWORD` from its own environment
  (set only for the one `subprocess.run` call, not the parent process's
  persistent environment) and answers git's username/password prompts
  from that — visible only via `/proc/<pid>/environ` to the same user,
  not via a process listing to everyone.
- **The push step also forces `-c credential.helper=` and `-c
  core.askpass=<script>` directly on the `git push` command line, not
  just `GIT_ASKPASS` in the environment.** Found from a real production
  report: `GIT_ASKPASS` alone wasn't enough — a system-wide `gitconfig`
  with its own `credential.helper` already configured (credential
  manager, `cache`, `store`, ...) takes priority over `GIT_ASKPASS` and
  can itself fail or attempt to prompt before our askpass script is
  ever consulted, surfacing as `fatal: could not read Username ...:
  terminal prompts disabled` — the exact symptom reported, with no
  indication our askpass had even run. `-c` options on the command line
  always win over anything in a config file, so overriding both there
  (only on the push invocation — `add`/`commit` need no credentials at
  all) is the one mechanism guaranteed to apply regardless of whatever's
  already configured on the host. `test_push_overrides_credential_helper_and_askpass`
  asserts this at the subprocess-argv level (mocking `subprocess.run` to
  inspect the actual command), since a local `file://` test remote never
  invokes a credential helper in the first place (confirmed by hand: a
  helper that always fails still lets a `file://` push through
  untouched) — there's no way to exercise the real failure end-to-end
  without a live HTTPS git server.
- **The git password is configured only by the main admin, in the same
  "⚙" settings dialog as the shared directory**, per explicit request
  ("Логин и пароль... будут редактироваться только у главного админа в
  настройках") — this required no new access-control code at all: that
  dialog's edit *form* already only renders when `not remote` (see the
  original multi-admin entry above), and `/config/shared-dir` already
  403s outright on a child relay. Stored in `AppConfig.git_push_username`/
  `git_push_password` in `config.json`, which now gets `chmod(0o600)` on
  every save (it didn't before — there was nothing secret in it until
  now) to match `admins.json`'s existing permissions. The password is
  never echoed back into the rendered form (`_render()` only passes a
  `git_push_password_set` boolean, never the value) — submitting
  `/config/shared-dir` with that field left blank keeps the previously
  saved password rather than clearing it, the same "blank means
  unchanged" convention a secret-field form always needs.

## Shared tools directory layout

```
<shared_dir>/
  tools/
    <name>/
      <module>.py           # self-describing: one function, name/docstring/__version__
    <doc-skill-name>/
      SKILL.md              (or skill.md) -- plain markdown, no frontmatter
      .skill_meta.json      # admin-supplied description, written at install time
      ... (any other supporting files/folders)
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
  file** can see `MAIN_HOST` and the Bitbucket project/repo/path the host
  address is read from — none of this is secret by itself, but it does
  mean the file shouldn't be posted somewhere wholly public if `MAIN_HOST`
  isn't meant to be.
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

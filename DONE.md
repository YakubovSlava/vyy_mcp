# Done

## 2026-10-19

- Scaffolded `jupyter_mcp_tools` package: `common/` (manifest validation,
  registry, dynamic loader, shared config), `mcp_server/` (FastMCP-based
  server), `admin_ui/` (FastAPI + Jinja2 upload/list/delete UI).
- Tool archive format defined and validated structurally (zip + manifest
  schema + AST check for the entrypoint function) without executing
  uploaded code — `common/manifest.py`.
- Install/list/remove lifecycle for shared tools on disk, including
  overwrite semantics — `common/registry.py`.
- MCP server dynamically (re)registers tools from the shared directory on
  a 5s poll loop via FastMCP's `add_tool`/`remove_tool`, so new/updated/
  removed tools take effect without restarting the server —
  `mcp_server/server.py`.
- Admin UI: single-page upload form, shared-directory configuration,
  install/remove actions, JSON `GET /api/tools` listing —
  `admin_ui/main.py` + `templates/index.html`.
- Example tool (`examples/echo_tool/`) + zip-builder script
  (`examples/build_archive.py`) for demoing the upload flow.
- Test suite (22 tests, all passing): manifest validation edge cases
  (bad zip, missing manifest, invalid name, missing entrypoint module/
  function, syntax errors, zip path traversal), registry install/list/
  remove/overwrite round-trips, admin API via `TestClient`, and MCP
  server tool sync (register/update/remove via `sync_tools`, verified by
  actually calling the tool through `FastMCP.call_tool`).
- Verified against the real `mcp` SDK (pinned `mcp<2`, installed 1.30.0)
  rather than assumptions — confirmed `FastMCP.add_tool`/`remove_tool`
  API shape, schema inference from type hints, and `call_tool` return
  shape by executing against the installed package.
- `pyproject.toml` with console script `jupyter-mcp-tools-server`,
  `[dev]` extra for pytest/httpx, editable-installed and tested in a
  local `.venv`.
- `README.md` (install/run/tool-archive-format), `PLAN.md` (architecture
  decisions + explicit trade-offs + open questions), this file.

- Admin UI is reachable from inside JupyterLab via `jupyter-server-proxy`'s
  *generic* port proxy (`.../user/<name>/proxy/<port>/`) — run as a plain
  background `uvicorn` process, no registration needed. (Initially built
  this as a named `jupyter_serverproxy_servers` entry point with a
  Launcher card instead; dropped that after the user clarified the real
  deployment only exposes apps through the generic `/proxy/<port>/` path,
  so a named-route registration would never actually be used — removed
  `jupyter_mcp_tools/serverproxy.py` and the corresponding `pyproject.toml`
  entry point/extra.)
- Made the admin UI proxy-prefix-safe regardless of which proxying method
  fronts it: `templates/index.html`'s form `action`s are relative
  (`tools/upload`, not `/tools/upload`), and `admin_ui/main.py`'s POST
  handlers (`/config/shared-dir`, `/tools/{name}/delete`) re-render the
  page directly instead of issuing a 303 redirect to `/` — an absolute
  redirect would have escaped the `/proxy/<port>/` prefix in the browser.
- Found and fixed a real (not just test-isolation) bug while adjusting
  tests for the above: `common/config.py`'s default paths
  (`DEFAULT_CONFIG_PATH`, `DEFAULT_SHARED_DIR`) were module-level
  constants evaluated once via `Path.home()` at import time, so they
  couldn't be overridden later. Refactored to compute them lazily in
  `config_path()`/`AppConfig.shared_dir_path` so overriding `HOME` (or
  not setting `shared_dir` at all) resolves correctly at call time. Also
  cleaned up a real `~/.jupyter_mcp_tools` directory this bug had caused
  to be created during manual smoke-testing.
- Updated `test_admin_api.py` for the no-redirect behavior and to isolate
  `HOME` per test (prevents real home-directory pollution going forward).
- Full suite still green after these changes (22/22), re-run twice back
  to back to confirm no state leaks into the real home directory.
- End-to-end verified the `/proxy/<port>/` access path against the user's
  actual local JupyterLab (not just assumed from docs): it 404'd at
  first because `jupyter-server-proxy` was installed in this project's
  own `.venv`, not in the venv the running `jupyter-lab` process
  (`/home/vyy/.../remote_connection/venv`) actually uses — two separate
  environments on the same host. Installed `jupyter-server-proxy` into
  the correct venv, JupyterLab was restarted, `jupyter server extension
  list` then showed `jupyter_server_proxy ... enabled`, and
  `https://<host>:8888/proxy/8766/` confirmed working (redirects to
  `/login` when unauthenticated, serves the admin UI correctly once
  logged in — user confirmed in their browser). Documented in README
  that `/proxy/<port>/` 404ing means the extension isn't enabled in the
  Jupyter server's *own* environment, not that the admin UI is broken.
- Redesigned the admin UI's look (`templates/index.html`): header with
  "MJ" (blue) + "App" (black) + "(Lab edition)" wordmark top-left, an
  address/settings button and a "+" add-skill button top-right, gray
  page background with white cards, native `<dialog>` modals for the
  upload and shared-dir forms, "tools" relabeled "skills" in the UI copy.
- Found (via the user's actual browser session, not assumption) that the
  generic `/proxy/<port>` route can serve this page without a trailing
  slash, which broke the plain-relative form actions added earlier: the
  browser resolved `tools/upload`/`config/shared-dir` one directory too
  high, producing requests like `POST /proxy/config/shared-dir` instead
  of `POST /proxy/8766/config/shared-dir`. Jupyter's own Tornado layer
  then rejected those with `403 '_xsrf' argument missing from POST` —
  confusing because the error looks like it's about CSRF/our app, but it
  actually comes from Jupyter rejecting a cross-site-looking POST to one
  of *its own* routes that no longer matched the intended proxy path.
  Diagnosed by reading the real Jupyter server's own log file
  (`jupyter.log`, found via `/proc/<pid>/fd/1`), which showed the exact
  mis-resolved request path and `Referer`. Fixed properly (see PLAN.md):
  `admin_ui/main.py` now reads the `X-Forwarded-Prefix` header
  `jupyter-server-proxy` already sends on every proxied request, and
  `templates/index.html`'s links/form actions are all built as
  `{{ base }}/<route>` instead of relative paths — correct regardless of
  trailing slash or how deep the current request path is. Added
  `test_form_actions_use_forwarded_prefix` and
  `test_form_actions_without_proxy_prefix_are_root_relative` to cover
  both cases. Full suite: 24/24 passing.

## Open — not yet done

- **No admin-only access control on the proxied UI** — see PLAN.md →
  "Open questions": any user who can open their own Jupyter server can
  reach it right now. Explicitly deferred by user decision, not an
  oversight — needs a follow-up task before this goes near a shared
  JupyterHub with non-trusted users.
- No CI configuration added (tests are run manually via `pytest`).

## 2026-10-19 (multi-admin / child-admin relay)

- Added `common/auth.py`: flat admin account store (`admins.json`),
  PBKDF2-HMAC-SHA256 hashed, no new dependency. `add_admin`/`verify_admin`/
  `remove_admin`/`list_admins`/`has_any_admin`.
- Added `admin_ui/manage_admins.py` + console script
  `jupyter-mcp-tools-admin-users` (`add [--password]`/`remove`/`list`) —
  how the main admin issues (and revokes) credentials for every other
  admin, including their own bootstrap account.
- `admin_ui/main.py` now requires HTTP Basic Auth (`require_admin`,
  checked against `common.auth`) on every route when running as the main
  host — this closes the "no auth on the admin UI" gap called out in the
  previous session's "Known limitations", though it's an app-level login
  independent of JupyterHub identity (see updated PLAN.md).
- Added `common/curl_client.py`: subprocess wrapper around the real `curl`
  binary (not a Python HTTP client library, per explicit requirement) —
  `get`/`post` with HTTP Basic Auth and multipart file upload support,
  parsing status code + body out of `curl -w '\n%{http_code}'`.
- `admin_ui/main.py` gained a second mode, selected by the
  `MCP_TOOLS_MAIN_HOST` env var: when set, every HTML route (index,
  upload, delete) forwards to a new JSON API (`/api/tools` GET/POST,
  `/api/tools/{name}/delete`) on the main host via `curl_client`,
  authenticated with `MCP_TOOLS_ADMIN_USER`/`MCP_TOOLS_ADMIN_PASSWORD`.
  This mode has no local Basic Auth of its own — the child admin's own
  Jupyter login is the trust boundary on that side. `config/shared-dir`
  is rejected with 403 in this mode (that setting is main-host-only).
- Added the JSON API itself (`/api/tools` GET/POST,
  `/api/tools/{name}/delete`) with real HTTP status codes (400
  validation, 409 duplicate, 404 missing) for machine callers, separate
  from the HTML routes (which always return 200 with an error banner in
  the body, since a browser needs a renderable page either way).
- `templates/index.html`: address dialog now branches on a `remote` flag
  — read-only "main host" info box in child mode instead of an editable
  shared-dir form; logo gains a small "· remote" suffix in that mode.
- Added `scripts/run_child_admin.sh`: the launcher a child admin actually
  runs (`run_child_admin.sh <main-host-url> <username> [port]`), prompts
  for the password via `read -s`, sets the three env vars above, then
  `exec`s the same `uvicorn ...admin_ui.main:app` command as the main
  host uses.
- Tests: `test_auth.py` (5 tests, store round-trips, no-plaintext-on-disk
  check). `test_admin_api.py` updated to bootstrap an admin and send
  Basic Auth on every call; added 4 new tests for the 401 paths (no
  creds, wrong password, unknown user, every mutating route). New
  `test_remote_mode.py`: spins up the admin UI as a **real** `uvicorn`
  subprocess on a real TCP port (not an in-process ASGI transport) acting
  as the main host, then drives a child instance through its HTML routes
  — those routes shell out to the real `curl` binary against that real
  subprocess, so the test exercises the actual main↔child transport, not
  just the Python call graph. 5 tests: empty list, bad credentials surface
  as an in-page error (not a crash), full upload→list→delete round trip,
  address dialog shows the main host read-only, and `config/shared-dir`
  correctly 403s from a child. Full suite: 38/38 passing.
- Manually exercised `jupyter-mcp-tools-admin-users add/list/remove`
  against a throwaway `admins.json` to confirm the CLI's actual output
  (generated password printed once, `list`/`remove` behave as documented)
  rather than just trusting the code read.
- Bootstrapped a real admin account (`slava`) on the user's actual host
  and confirmed unauthenticated requests get rejected against the real
  running server, not just in tests.

## 2026-10-19 (login page instead of the browser's Basic Auth popup)

- Replaced the browser-facing login mechanism: the user asked for a
  normal in-page login form instead of the native HTTP Basic Auth
  popup. Added `common/session.py` — signed, expiring session cookies
  (HMAC-SHA256 over `username:expiry`, random per-install secret key on
  disk, 12h TTL, no new dependency). Added `admin_ui/templates/login.html`
  styled consistently with the rest of MJApp.
- `admin_ui/main.py`: split the single Basic-Auth dependency into two —
  `require_admin_page` (cookie session; raises `NotAuthenticated`, caught
  by an exception handler that redirects to `/login`) for the HTML routes,
  and `require_admin_api` (unchanged HTTP Basic Auth) for the JSON
  `/api/...` routes that child relays' `curl` calls hit. A session cookie
  does not grant API access and a Basic Auth header does not grant HTML
  access — verified by a dedicated test
  (`test_api_requires_basic_auth_not_session_cookie`).
- Added `GET/POST /login` and `POST /logout`; `index.html` gained a
  "⎋ Sign out" button (hidden in child/remote mode, which still has no
  local login of its own).
- Rewrote `test_admin_api.py`'s fixtures: `anon_client` (no session) and
  `client` (logs in via `POST /login` in the fixture itself, like a real
  browser would) replace the old `.auth = (user, pass)` pattern for HTML
  routes; `/api/tools` calls in tests now pass `auth=` explicitly per
  request since that's the one surface that still uses it. Added tests
  for the login/logout/redirect flow and for the two-auth-gates
  separation. Full suite: 42/42 passing.
- Found (via the user's real browser session against the real Jupyter
  server, not assumption) that the login redirects were doubling the
  proxy prefix: `.../proxy/8766/proxy/8766/login`, a 404. Root cause,
  confirmed from the real Jupyter server's log: `jupyter-server-proxy`
  already rewrites `Location` headers on redirect responses to prepend
  its own context path — our `_redirect_to_login`/`login_submit`/`logout`
  handlers were *also* prepending `X-Forwarded-Prefix` manually,
  doubling it. Fixed by making all three redirects prefix-free (plain
  `/login`, `/`, `/login`) and letting `jupyter-server-proxy` add the
  prefix, while HTML body links (forms' `action=` attributes) keep the
  manual `{{ base }}/<route>` prefixing, since proxy rewriting only
  touches `Location` headers, not arbitrary response bodies. Re-ran the
  full suite after the fix (still 42/42) and verified the actual
  `uvicorn` process restarted cleanly.

## 2026-10-19 (end-user onboarding: notebook, invites, user tracking)

- Added `common/users.py`: flat JSON store (`users.json`) tracking end
  users — `create_invite`/`get_invite`/`record_notebook_registration`/
  `record_mcp_call`/`list_users`. Two independent paths populate the same
  store: an admin-issued invite code (visible in the UI the moment it's
  created, before the user does anything) and upsert-by-username from
  either the notebook's self-registration or the MCP server's own
  per-call logging.
- `common/config.py`: added `bitbucket_script_path` to `AppConfig`
  (configurable from the same "⚙" dialog as `shared_dir`) — the path to
  an external, admin-provided script (not written by this project) that
  resolves the current MCP host address from Bitbucket given a
  username/password.
- Added `admin_ui/notebook_builder.py`: builds a valid `.ipynb` (nbformat
  4) with two code cells — resolve host via the configured script,
  then print the MCP connection command (OS username auto-filled via
  `getpass.getuser()`) and self-register back to the main host via
  `curl`. Hit and fixed a real bug while building this: a nested f-string
  meant to embed a literal `code=<value>` form field instead evaluated to
  Python's `code='<value>'` *inside* the generated source, a syntax
  error — caught by actually `ast.parse()`-ing the generated cells rather
  than trusting the template, not by inspection.
- `admin_ui/main.py`: added `POST /users/invite` (admin-gated, same as
  other mutating routes), `GET /connect/<code>` (public — serves the
  generated notebook as a download, 404 on unknown code, 503 if no
  Bitbucket script is configured yet), and `POST /api/users/register`
  (public — what the notebook's `curl` call hits). "Public" here means no
  admin login and no Basic Auth, by explicit decision: end users have no
  account to authenticate with; see PLAN.md for the trade-off this
  implies (anyone who can reach the main host can self-register an
  arbitrary username).
- `templates/index.html`: new "Users" section (local/main mode only)
  listing invited/connected end users with last-seen time, and an
  "Invite a user" dialog.
- `mcp_server/server.py`: added `LogMCPUserMiddleware` — reads a plain
  `X-MCP-User` header and upserts into the same `users.json` the admin UI
  reads. Only wired up for the `streamable-http` transport (`stdio` has
  no remote-caller identity to log at all). Required bypassing
  `FastMCP.run()` — it has no hook to attach Starlette middleware — in
  favor of building the ASGI app via `app.streamable_http_app()` and
  serving it with `uvicorn.run()` directly.
- Tests: `test_users.py` (7, store round-trips including the
  invite-code/MCP-call convergence case), `test_user_provisioning.py` (8,
  the admin-UI endpoints — invite creation, 404/503 edge cases, a real
  generated notebook validated with `ast.parse`, both registration paths,
  public-vs-admin-gated route checks), `test_mcp_user_logging.py` (2,
  against the real `streamable_http_app()` + middleware via
  `starlette.testclient.TestClient` used as a context manager so the
  session-manager lifespan actually runs). Full suite: 59/59 passing.
- Manually verified the middleware records a caller even when the
  downstream MCP protocol handshake itself fails/500s (expected without a
  real MCP client) — confirmed the recording happens in the middleware
  layer regardless of what the wrapped app does with the request, via a
  throwaway run against a real `TestClient`+header before writing the
  permanent lifespan-aware test.
- Added `scripts/mock_resolve_host.py`: a stand-in for the real,
  not-yet-existing Bitbucket host-lookup script — same calling
  convention (username argv, password on stdin, address on stdout), but
  ignores the credentials and always prints `http://127.0.0.1:8765`, so
  the invite → notebook → connect flow can be exercised end-to-end before
  the real script exists. Pointed the real running main host's config at
  it (`bitbucket_script_path`) and confirmed it round-trips correctly via
  `common.config.load_config()`.

## 2026-10-19 (replaced the invite-code flow with one static distributed file)

- Per explicit decision, dropped the per-invite-code generated notebook
  (`POST /users/invite`, `GET /connect/<code>`, the "Invite a user"
  dialog) in favor of one real, committed file:
  **`notebooks/connect_to_mcp.ipynb`**. Rebuilt with
  `scripts/build_connect_notebook.py <main-host-url> <bitbucket-script-path>`,
  a thin CLI wrapper around the existing `admin_ui/notebook_builder.py`
  (now simplified: dropped the `invite_code` parameter, and the host
  address / script path are written into the notebook's first code cell
  as plain `MAIN_HOST`/`BITBUCKET_SCRIPT_PATH` constants an admin can
  also hand-edit directly, without regenerating).
- Simplified `common/users.py` to match: one function,
  `record_user(username)`, replaces the old
  `create_invite`/`get_invite`/`record_notebook_registration` trio —
  there's no invite code to associate a sighting with anymore, just a
  flat upsert-by-username. `mcp_server/server.py`'s
  `LogMCPUserMiddleware` and `admin_ui/main.py`'s
  `POST /api/users/register` both call this one function now.
- Removed `/users/invite` and `/connect/{code}` from `admin_ui/main.py`;
  `/api/users/register` stays (still public, still what the notebook's
  self-registration `curl` call hits) but no longer takes a `code` field.
  `templates/index.html`'s "Users" section lost the "Invite a user"
  button/dialog and now just lists username + first/last seen.
- Generated the real file into the repo
  (`scripts/build_connect_notebook.py http://127.0.0.1:8766
  .../scripts/mock_resolve_host.py`) and verified both its code cells
  parse as valid Python via `ast.parse` before committing it.
- Replaced `test_users.py` and `test_user_provisioning.py` (invite-code
  assumptions throughout) with `test_users.py` (rewritten for the
  simpler API), `test_user_registration_api.py` (the surviving
  registration endpoint + "Users" list rendering), and
  `test_notebook_builder.py` (the simplified builder, including that the
  constants actually land in the generated source). Full suite: 54/54
  passing. Grepped for any leftover references to the removed functions
  (`record_mcp_call`, `create_invite`, `get_invite`,
  `record_notebook_registration`) across the package — none found.

## 2026-10-19 (page split, nav polish, real server bugfix, multi-file skill results)

- Found and fixed a real crash on the user's actual running admin UI:
  `GET /` 500'd with `TypeError: '<' not supported between instances of
  'NoneType' and 'str'` in `users.json`'s sort — caused by leftover
  entries from the old invite-code schema (`last_seen: null`) mixed in
  with new-schema entries after the earlier migration. `list_users()`
  now filters to entries matching the current schema instead of crashing
  on anything unexpected (same defensive posture as
  `registry.list_tools`); added a regression test reproducing the exact
  legacy record shape. Cleaned the real `~/.jupyter_mcp_tools/users.json`
  on the user's host.
- Split the single admin UI page into three, per explicit request:
  `templates/base.html` (shared header/styles/dialogs-block via Jinja
  inheritance) with `index.html` (Skills, at `/`), `users.html` (Users,
  at `/users`), both `extends`-ing it. `/users` 404s on a child relay
  (no local users to show).
- Moved the Skills/Users nav links into the header itself, next to the
  logo — first attempt centered them via `justify-content: space-between`
  across three flex children, which visibly shifted position between
  pages because the right-hand `header-actions` content differs in width
  (Skills page has two extra buttons, Users page just has sign-out).
  Fixed by wrapping logo+nav in one `.header-left` flex group so
  `space-between` only ever applies between that group and
  `header-actions` — nav position is now fixed regardless of what's on
  the right.
- Added a fourth page, **Docs** (`/docs`), documenting the skill file
  structure (manifest.json format, entrypoint convention, the two
  install paths, how fast changes propagate) directly in the admin UI
  instead of only in README. Hit a real collision while building it:
  FastAPI registers its own Swagger UI at `/docs` by default, which
  silently won the route and served Swagger instead of our template —
  caught by the test suite actually asserting on page content, not by
  inspection. Fixed by disabling FastAPI's built-in docs
  (`docs_url=None, redoc_url=None, openapi_url=None`), which we don't
  use anyway.
- Added multi-file skill results: `common/skill_results.py`
  (`SkillFile`, `files_result()`) standardizing the JSON shape for a
  skill that hands back files for the *calling human* to save and run
  under their own account, rather than a text answer — requires zero MCP
  server changes, since FastMCP already serializes any JSON-serializable
  return value straight into the response (verified directly: installed
  `examples/setup_files_tool` into the real running shared directory,
  called it through an actual `mcp` SDK client session against the live
  server, confirmed the structured JSON came back correctly, then
  removed the live install so it doesn't linger as a permanent skill).
  Documented the convention on the new Docs page and in README.
- Tests: `test_docs_page.py` (login gate, manifest example present, the
  new multi-file-result section present, configured shared_dir shown,
  nav links present), `test_skill_results.py` (shape + JSON-serializable),
  a new case in `test_server_sync.py` round-tripping the real
  `examples/setup_files_tool.zip` through install → sync → call, plus
  remote-mode/users-page test updates for the new `/docs` route. Full
  suite: 66/66 passing.
- Pushed the project to GitHub (`git@github.com:YakubovSlava/vyy_mcp.git`,
  branch `main`) — this was the first commit ever made in the repo (all
  prior work had been uncommitted on disk). Verified no secrets/venv
  ended up staged before committing.
- Moved dependency declarations out of `pyproject.toml` into
  `requirements.txt` (runtime) / `requirements-dev.txt` (+ pytest/httpx),
  per explicit request. `pyproject.toml` now declares
  `dynamic = ["dependencies"]` and points at `requirements.txt` via
  `[tool.setuptools.dynamic]` instead of listing packages twice. Updated
  README's install steps to `pip install -r requirements-dev.txt` then
  `pip install -e . --no-deps`; re-verified the console scripts
  (`jupyter-mcp-tools-server`, `jupyter-mcp-tools-admin-users`) still get
  registered and the full suite (67/67) still passes under the new
  install flow.

## 2026-10-19 (shared data_dir for admins/config/users/session_secret)

- Added `common/paths.py`'s `data_dir()` — a single `MCP_TOOLS_DATA_DIR`
  env var (default `~/.jupyter_mcp_tools`) that `common/auth.py`,
  `common/config.py`, `common/users.py`, and `common/session.py` all now
  fall back to for `admins.json`/`config.json`/`users.json`/
  `session_secret`, instead of each hardcoding the same path
  independently. Per explicit request, moved out of the project
  checkout entirely. Each file's own specific override env var
  (`MCP_TOOLS_ADMINS_PATH` etc.) still wins if set. `shared_tools/` (the
  actual skill content) deliberately wasn't moved — it's managed through
  the admin UI, not an app secret.
- Migrated the real, already-deployed data on the user's host: moved
  `admins.json`, `config.json`, `session_secret`, `users.json` from
  `~/.jupyter_mcp_tools/` to `/home/vyy/.../remote_connection/` (one
  level above the project checkout), restarted both live services
  (`admin_ui` on 8766, `jupyter-mcp-tools-server` on 8765) with
  `MCP_TOOLS_DATA_DIR` set, and verified the existing admin account
  (`slava`) still resolves correctly from the new location — confirmed
  via `common.auth.list_admins()` against the real env, not just tests.
  `~/.jupyter_mcp_tools/` now contains only `shared_tools/`, as intended.
- Added `test_paths.py` (4 tests: default, override, all-four-share-it,
  specific-override-still-wins). Full suite: 71/71 passing.

## 2026-10-19 (fixed URL handling for real JupyterHub — relative links + a known-working reference)

- User reported `/user/<username>/proxy/<port>` redirecting into `/lab`
  on their actual production deployment — a **real JupyterHub**, not the
  standalone jupyter-lab this project had been dev-tested against so far.
  The `X-Forwarded-Prefix`-based link-building from the previous session
  (see PLAN.md's superseded entry) depends on `jupyter-server-proxy`
  setting that header and rewriting `Location` headers on redirects —
  neither of which JupyterHub's own front-door proxy
  (`configurable-http-proxy`) does; it's a dumb passthrough. With the
  header absent, links collapsed to plain absolute paths that escaped
  the Hub's `/user/<name>/...` prefix in the browser.
- Diagnosed by reading the real prod Jupyter log line the user pasted
  (`"GET / HTTP/1.1" 303 See Other`) — confirmed the request had already
  lost its `/user/.../proxy/...` prefix by the time it reached the
  single-user server, pointing at the Hub's proxy layer rather than this
  app.
- Found and inspected a **known-working sibling project on the same
  Hub** (`github.com/YakubovSlava/vyy_trees`, cloned directly for
  inspection rather than guessing) to see what pattern actually survives
  that specific proxy. It uses exactly two things: every link/fetch URL
  is a plain relative path with no leading `/` at all, and a single
  inline `<script>` at the top of `<head>` that redirects client-side to
  add a trailing slash if the current URL doesn't have one
  (`location.replace(location.pathname + "/")`) — no server-side
  URL-building logic, no proxy headers read anywhere.
- Ported that exact pattern: `templates/base.html` and `templates/login.html`
  gained the trailing-slash self-correct script; removed
  `_request_base()`/`X-Forwarded-Prefix` entirely and every
  `{{ base }}/<route>` in the templates is now a plain relative path
  (`action="tools/upload"`, `href="users"`, `href="."` for the root).
  For server-side redirects (where a relative `Location` must still
  resolve against whatever depth the *current* route happens to be at),
  added `_relative_redirect()` in `admin_ui/main.py`: computes the right
  number of `../` from `request.url.path`'s segment count rather than
  assuming a fixed depth or relying on any proxy to fix it up. Verified
  the computed values directly with `curl` against the real running
  admin UI at every route depth (root → `login`, `/tools/upload` →
  `../login`, `/tools/{name}/delete` → `../../login`,
  `/config/shared-dir` → `../login`) before touching the test suite.
- Added a caveat to `jupyter_ext.py`'s docstring: don't enable it on a
  real Hub — there, `/user/<name>/` is a genuine prefix the Hub's proxy
  already understands and strips before the request ever reaches this
  extension's handler, so it would be dead code at best.
- Updated ~20 test assertions across `test_admin_api.py`,
  `test_docs_page.py`, `test_user_registration_api.py`,
  `test_remote_mode.py` that had hardcoded the old absolute-path/
  `X-Forwarded-Prefix` expectations, replacing the two
  forwarded-prefix-specific tests with one `test_form_actions_are_relative`.
  Full suite: 70/70 passing (one net fewer test than before — two old
  prefix-specific tests collapsed into one, since there's no longer a
  "with prefix" vs "without prefix" distinction to test separately).

## 2026-10-19 (fixed the root-page login redirect still dropping a path segment)

- User reported the *just-shipped* relative-links fix still broke on
  prod: logging in redirected to `.../proxy/login` instead of
  `.../proxy/8766/login` — a 404. Root cause: the app **root** path
  (`/`) has no segment of its own, so whether a relative `"login"`
  resolves correctly depends entirely on whether the browser's current
  URL happens to already end in `/` at the moment the redirect fires —
  and the previous fix's `_relative_redirect()` computed its `../`
  count from the *backend's* view of the path (always `0` for root),
  while the *browser* resolves the actual `Location` header against its
  own (proxy-stripped, prefix-including) URL, which may or may not have
  a trailing slash. Every *other* route (`/users`, `/tools/upload`, ...)
  has a real trailing segment and was already correct — this was
  specifically a root-only gap.
- Fixed by not computing a redirect for the root case at all: when
  `NotAuthenticated` fires on `request.url.path == "/"`,
  `admin_ui/main.py` now renders `login.html` directly (status 200, no
  `Location` header) instead of redirecting. Its self-correct script —
  which operates purely client-side on `location.pathname`, which the
  *browser* always sees correctly regardless of how much prefix an
  upstream proxy already stripped before the request reached this
  backend — fixes the trailing slash first, then the already-correctly
  anchored login form submits normally.
- Caught and fixed a second latent bug while implementing this: the
  trailing-slash self-correct script cannot live on every page the way
  it was initially (over-eagerly) ported in — `/login`, `/users`,
  `/docs` all have a real trailing segment of their own, and that
  script would wrongly try to turn e.g. `/login` into `/login/`, which
  404s (no route registered with a trailing slash). Re-inspected
  `vyy_trees` more carefully and confirmed it only puts the script on
  `index.html` (its root), never on `tree.html` (its one other leaf
  page) — had missed that distinction on the first port. Fixed by
  moving the script into a Jinja block (`self_correct_script` in
  `base.html`) that only `index.html` overrides, and making
  `login.html`'s copy conditional on a `self_correct` flag set only by
  the root's `NotAuthenticated` fallback — a normal `GET /login` visit
  no longer gets it at all.
- Updated `test_unauthenticated_html_routes_redirect_to_login` and
  `test_logout_clears_session` for the new root behavior (200 + login
  content, not a redirect), and added
  `test_login_page_itself_has_no_self_correct_script` as a direct
  regression test for the second bug. Verified against the real running
  admin UI with `curl`: `GET /` unauthenticated now returns 200 with the
  self-correct script present; `GET /login` returns 200 with it absent.
  Full suite: 71/71 passing.

## 2026-10-19 (fixed MCP server rejecting every proxied request with 421)

- User reported the MCP server (not the admin UI this time) always
  returning `421 Misdirected Request` when connecting through opencode.
  Root-caused by reading `mcp/server/transport_security.py`'s source
  directly rather than guessing from the error: the `mcp` SDK's
  `FastMCP` auto-enables DNS-rebinding protection — validating the
  `Host` header against `127.0.0.1`/`localhost`/`::1` patterns — if it
  sees one of those as its `host` constructor argument. `build_app()`
  in `mcp_server/server.py` was constructing `FastMCP(...)` with no
  `host` kwarg (silently defaulting to `"127.0.0.1"`), then `main()`
  separately set `app.settings.host = args.host` afterwards when
  binding `0.0.0.0` for real use — too late, since the host-locking
  decision is baked in at `__init__` time and doesn't get revisited.
  Every request through any real proxy (JupyterHub, opencode hitting a
  public domain, curl with a non-local `Host` header) got 421'd by this
  SDK-internal check, which had nothing to do with our own code and
  made the error look unrelated to anything we'd written.
- Fixed by passing `host=`/`port=` straight into `build_app()`'s own
  `FastMCP(...)` call (new keyword args, default `127.0.0.1`/`8765` to
  match prior behavior) instead of `main()` patching `app.settings`
  after construction. Binding `0.0.0.0` now correctly skips FastMCP's
  auto-protection (its own `host in (...)` check is false), matching
  this project's existing "trusted internal/proxied deployment" trust
  model.
- Verified directly against the real running server: a full `mcp` SDK
  client handshake (`initialize` → `list_tools`) carrying a `Host:
  yakubov.tech:8888` header now succeeds where it previously 421'd.
  Added `test_binding_0_0_0_0_accepts_foreign_host_header` and a
  contrasting `test_binding_127_0_0_1_still_locks_host_header` (confirms
  the fix is "pass `host` through correctly," not "disable the check
  globally"). Full suite: 73/73 passing.

## 2026-10-19 (second skill bundle kind: SKILL.md-based doc skills)

- Added `common/doc_skill.py`: parses `SKILL.md`/`skill.md` (YAML
  frontmatter + markdown body, Claude's "Agent Skills" format) into a new
  `DocSkillManifest` dataclass (`name`, `description`, `body`, `version`,
  `files`), with `validate_doc_skill_archive`/`validate_doc_skill_dir` for
  zip- and on-disk validation respectively — structural only (frontmatter
  is valid YAML, required fields present, name matches the existing
  `NAME_RE`, body non-empty, no path-traversal members), never executing
  anything, same posture as `common/manifest.py`.
- Rewrote `common/registry.py` to treat code skills (`manifest.json`) and
  doc skills (`SKILL.md`) as one polymorphic `AnyManifest = Manifest |
  DocSkillManifest` union: `install_tool`/`list_tools` detect which kind
  an archive/folder is by presence of `manifest.json` vs. `SKILL.md`
  rather than requiring an explicit kind field, so both can coexist in
  the same shared directory. Removed the unused `get_tool()` (confirmed
  via grep nothing referenced it). Found and fixed a latent bug while
  doing this: the new `_validate_archive` called `zipfile.ZipFile()`
  directly without an `is_zipfile()` guard first, so a non-zip upload
  raised an unhandled `BadZipFile` instead of the clean
  `ToolValidationError` the admin UI expects — broke
  `test_upload_invalid_archive_shows_error` until fixed.
- `mcp_server/server.py`: `sync_tools()` now branches on manifest type —
  doc skills get a tool built by the new `_make_doc_skill_tool`, instead
  of `load_entrypoint`. The generated tool implements progressive
  disclosure per explicit decision (asked via AskUserQuestion: SKILL.md +
  file listing first vs. everything in one call — chose the former): a
  no-argument call returns the `SKILL.md` body plus a listing of the
  skill's other files; a call with `file="<path>"` returns that file's
  text content, or a clear error listing valid paths otherwise. The
  frontmatter `description` is passed straight into `FastMCP.add_tool` as
  the tool's own description — the actual point of the feature, per
  explicit request: "на уровне инструмента должно задаваться описание,
  чтобы агент понимал, когда нужно обращаться к конкретному скиллу" — so
  a connecting agent can decide whether to call a given skill purely from
  `tools/list`, without reading `SKILL.md`'s body first.
- Added `manifest.kind`/`DocSkillManifest.kind` (`"code"`/`"doc"`)
  properties purely for the admin UI to label skills with. Found that
  `dataclasses.asdict()` silently drops `@property` fields — fixed
  `registry.manifest_to_dict()` to merge `kind` in explicitly rather than
  relying on `asdict()` alone.
- Admin UI: `templates/index.html`'s skill rows now show a small
  "code"/"SKILL.md" badge (new `.skill-kind` CSS in `base.html`) next to
  each skill's name, and the "Add a skill" dialog's help text mentions
  SKILL.md as an alternative upload format. `templates/docs.html` gained
  a full "Doc skills: SKILL.md" + "Calling a doc skill: progressive
  disclosure" section alongside the existing manifest.json documentation,
  and the "What a skill looks like on disk" tree now shows both layouts.
- Added `examples/repo_explainer/` (a real `SKILL.md` + `references/
  architecture.md`) as the example doc skill, used both in the Docs page
  references and in tests — zipped on the fly via the existing
  `_zip_example()` test helper, not committed as an archive (per the
  project's "no zip archives in the repo" rule).
- Tests: new `test_doc_skill.py` (13 tests — frontmatter parsing, missing/
  invalid frontmatter, zip/dir validation happy paths and every failure
  mode: not-a-zip, missing SKILL.md, missing required field, invalid
  name, path traversal, empty body). `test_registry.py` gained 4 tests
  (install/list a doc skill, code+doc skills coexisting, archive with
  neither manifest.json nor SKILL.md raising, `manifest_to_dict` working
  for a doc skill). `test_server_sync.py` gained
  `test_doc_skill_round_trip`, exercising the real `repo_explainer`
  example through install → sync → call for the no-arg, `file=`, and
  nonexistent-file cases. `requirements.txt` gained `pyyaml>=6.0`. Full
  suite: 91/91 passing.
- Verified end-to-end against the real, already-running MCP server (not
  just unit tests): installed the `repo_explainer` example into the live
  shared directory, hit "unknown tool" at first because the long-running
  server process still had the pre-change `registry.py`/`doc_skill.py`
  loaded in memory — restarted it
  (`pkill -f jupyter-mcp-tools-server` then relaunched with the same
  `MCP_TOOLS_DATA_DIR`/transport/host/port as before), then ran a full
  `initialize()` → `list_tools()` → `call_tool()` round trip through a
  real `mcp` SDK client session and confirmed the tool's description and
  both calling conventions (no-arg, `file=`) worked as designed.

## 2026-10-20 (admin UI translated to Russian)

- Translated every user-facing string in the admin UI to Russian, per
  explicit request: `templates/base.html` (nav tabs, sign-out, error
  banner), `templates/login.html` (title, labels, button, error message),
  `templates/index.html` (skill rows, empty state, both dialogs),
  `templates/users.html` (section, empty state), `templates/docs.html`
  (every section). `admin_ui/main.py`'s banner messages ("installed for
  all users", "removed for all users", "could not reach main host", etc.)
  were translated too, since they render into the same banners. Left in
  English on purpose: the `ToolValidationError`/etc. exception message
  text (internal/dev-facing, and pinned by existing `match=` regexes in
  several test files — translating it would've meant rewriting those
  tests for no user-facing benefit), and the "MJApp" brand name itself.
- Updated every test asserting on the old English strings
  (`test_admin_api.py`, `test_remote_mode.py`,
  `test_user_registration_api.py`) to match the new Russian text. Full
  suite: 91/91 passing.
- Restarted both live processes (admin UI on 8766, MCP server on 8765)
  against the real `MCP_TOOLS_DATA_DIR` to pick up the change, verified
  via `curl` that an unauthenticated `GET /` now renders the Russian
  login page.

## 2026-10-20 (code skills no longer need manifest.json)

- Per explicit request ("Модуль с документацией выглядит некрасиво.
  Сделай так, чтобы нам не нужны были manifest json" — the separate JSON
  manifest was ugly, redundant ceremony around a single function),
  rewrote `common/manifest.py` so a code skill is just one self-describing
  `.py` file: the archive/folder must contain exactly one top-level `.py`
  file with exactly one top-level function. That function's **name**
  becomes the skill/tool name (validated against the same `NAME_RE` as
  before), its **docstring** becomes the description (required — upload
  is rejected without one), and an optional module-level `__version__ =
  "..."` string literal sets the version (defaults to `"1.0.0"`).
  `entrypoint` is computed as `f"{module_stem}:{func.name}"` instead of
  being declared. The `parameters` field is gone entirely — it was
  already dead, documentation-only weight nothing read (the real MCP
  schema has always come from the function's type hints, per the
  existing "Input schema comes from Python type hints" decision in
  PLAN.md). Chose AskUserQuestion's "metadata from
  docstring/signature" option over "drop code skills entirely in favor
  of SKILL.md" or "just restyle the Docs page" — the first because
  Python skills with real type-hinted parameters are still useful and
  distinct from markdown-only doc skills; the second because the user's
  complaint was about needing the file at all, not just how it's
  displayed.
- `common/registry.py`'s bundle-kind detection no longer checks for
  `manifest.json` — it now checks for a `.py` file at the root instead
  (`root_python_files()`, new in `manifest.py`), and re-reading an
  already-installed code skill (`_load_installed`) calls a new
  `validate_dir()` that re-derives the manifest from the on-disk `.py`
  file every time, the same way `validate_doc_skill_dir` already did for
  doc skills — no manifest is ever cached to a file on disk for either
  kind now.
- Migrated both real examples to the new format by simply deleting their
  now-redundant `manifest.json` (both `tool.py` files already had
  docstrings on their single function, so no code changes were needed):
  `examples/echo_tool/`, `examples/setup_files_tool/`. Also removed a
  stray `examples/repo_explainer.zip` and `examples/.ipynb_checkpoints/`
  that had accumulated from earlier manual testing — zips aren't allowed
  in the repo per existing convention.
- Admin UI: `templates/docs.html`'s "Code skills" section rewritten to
  show the new single-function convention instead of a manifest.json
  example; `templates/index.html`'s "Add a skill" dialog help text
  updated to match. `PLAN.md`/`README.md` updated with the new format
  and the architecture reasoning above.
- Tests: rewrote `test_manifest.py` for the new AST-derived-manifest API
  (valid archive, not-a-zip, no .py at root, more than one .py at root,
  invalid function name, more than one top-level function, no top-level
  function, missing docstring, syntax error, custom `__version__`
  picked up, path traversal) — 11 tests, up from 8. Updated
  `conftest.py`'s `make_archive`/`VALID_MODULE` fixtures and every caller
  across `test_registry.py`, `test_server_sync.py`, `test_admin_api.py`,
  `test_remote_mode.py` to stop writing a `manifest.json` member.
  `test_docs_page.py`'s manifest-example assertion replaced with one
  checking the new single-function example renders and `manifest.json`
  is absent from the page. Full suite: 94/94 passing.

## 2026-10-20 (connection notebook: opencode + real Bitbucket REST code)

- Per explicit request, the connection notebook's "print the connection
  command" cell now prints commands for **both** MCP clients — the
  existing `claude mcp add ...` plus a new
  `opencode mcp add shared-tools --url <mcp_url> --header
  "X-MCP-User=<username>"` line, matching the exact syntax the user gave
  (`opencode mcp add NAME --url ... --header "X-MCP-USER=..."`).
- Replaced the pluggable-external-script Bitbucket lookup with real,
  directly-runnable code, per explicit request ("сделай так, чтобы код я
  прям вставил по извлечению данных с bitbucket" — give real code to
  paste in, not a placeholder script path). `notebook_builder.py`'s
  lookup cell now does an actual `requests.get(...)` against a Bitbucket
  Server/Data Center REST endpoint
  (`<base>/rest/api/1.0/projects/<project>/repos/<repo>/raw/<path>`) with
  HTTP Basic Auth, assuming self-hosted Bitbucket Server/Data Center
  (not Bitbucket Cloud, whose API is shaped differently) as the far more
  likely setup behind an internal JupyterHub. `BITBUCKET_PROJECT`/
  `BITBUCKET_REPO`/`BITBUCKET_FILE_PATH` are baked into the notebook with
  placeholder values (`"PROJ"`/`"mcp-config"`/`"host.txt"`) the admin
  hand-edits to point at wherever the org actually keeps the current MCP
  host address — same bake-in-constants pattern `MAIN_HOST` already used.
  Added `requests>=2.31` to `requirements.txt` (new runtime dependency —
  this project had only used `curl` subprocess calls for HTTP so far;
  `requests` was simpler here since this code runs on the *end user's*
  machine inside their own notebook, not as part of this project's own
  "main↔child goes over curl" requirement, which only applies to
  admin-to-admin traffic).
- Renamed `AppConfig.bitbucket_script_path` → `bitbucket_base_url`
  throughout (`common/config.py`, `admin_ui/main.py`,
  `templates/index.html`'s "⚙" dialog) — it now just displays the
  Bitbucket instance's base URL as a reminder of what's baked into the
  distributed notebook; it was never read at request time by anything
  even before this change. Deleted `scripts/mock_resolve_host.py` (the
  old script-based flow's local stand-in) — there's no external script
  left to stand in for.
- `scripts/build_connect_notebook.py`'s second CLI argument changed from
  a script path to a Bitbucket base URL; regenerated the real committed
  `notebooks/connect_to_mcp.ipynb` and confirmed both generated code
  cells still parse as valid Python via `ast.parse`.
- Tests: rewrote `test_notebook_builder.py` for the new
  `build_notebook()` signature (`bitbucket_base_url` +
  `bitbucket_project`/`bitbucket_repo`/`bitbucket_file_path` instead of
  `bitbucket_script_path`), added assertions that the lookup cell
  contains the real `requests.get` call and REST path shape, and that
  the connect cell prints both the `claude` and `opencode` commands.
  Full suite: 96/96 passing.

## 2026-10-20 (fixed uploads redirecting straight to the domain root on prod)

- User reported "Установить и поделиться со всеми" redirecting to
  `addr/tools/upload` — a 404, with the whole `/user/<name>/proxy/<port>`
  prefix missing. Root-caused by reproducing it locally rather than
  guessing: `POST /tools/upload/` (note the trailing slash) triggers
  Starlette's own built-in `redirect_slashes` behavior, which 307s to
  `http://testserver/tools/upload` in tests — an **absolute** Location
  header (scheme+host+path), built with zero awareness of any proxy
  prefix. This is a completely different hole from the one
  `_relative_redirect()` already closes (see PLAN.md): that function
  only covers redirects *this app* emits; Starlette's automatic
  trailing-slash redirect fires before our own routing code ever runs,
  so it was never going through that discipline at all. An earlier,
  separately-reported `/hub/tools/upload` 404 from the same user turned
  out to be a red herring from a stale admin UI process still running
  pre-fix code in memory (confirmed nothing in this codebase ever
  constructs an `/hub/...` path) — restarting it was enough to rule that
  one out, but it didn't explain the reproducible `addr/tools/upload`
  case, which needed this actual fix.
- Fixed by `FastAPI(..., redirect_slashes=False)` in `admin_ui/main.py`,
  plus a new `StripTrailingSlashMiddleware` that strips a trailing slash
  from `scope["path"]` before routing — so whichever proxy in the chain
  is adding one (unconfirmed which, since this project doesn't have
  direct access to the production Hub to inspect) hits the route
  directly, with no redirect of any kind involved.
- Added `test_trailing_slash_never_triggers_a_redirect` reproducing the
  exact request shapes from the report (`POST /tools/upload/`, `GET
  /users/`, `GET /docs/`) and asserting 200. Verified directly against
  the real running admin UI with `curl` too (trailing-slash requests now
  get our own relative `../login`/`login` redirects when unauthenticated,
  never an absolute `http://...` one). Full suite: 97/97 passing.

## 2026-10-20 (deleting an already-gone skill showed a bare JSON error page)

- User reported clicking "Удалить" on a skill as "not working," producing
  a raw `404` response. Captured the actual request/response from the
  browser's Network tab: `Server: uvicorn`, a small `application/json`
  body — this was genuinely *our own app* responding, not a proxy
  problem this time, and the tool really was already gone (confirmed by
  reading the real shared directory directly: `tools/` was empty, and
  the admin UI's own log showed an earlier `POST /tools/echo/delete`
  that had already succeeded with `200` — the second click was against a
  stale page that still showed the already-deleted row).
- The underlying bug this surfaced: `delete_tool` in `admin_ui/main.py`
  raised a bare `HTTPException(404)` on `ToolNotFoundError` for both the
  main-host and child-relay code paths, which FastAPI renders as a
  plain unstyled `{"detail": "..."}` JSON page with no navigation — a
  direct violation of this project's own documented convention (PLAN.md:
  "HTML routes return 200 with an error banner in the body... since a
  browser needs a renderable page either way"), which every *other*
  mutating route (`tools/upload`, `config/shared-dir`) already follows.
  A double-click, or any stale page after someone else removed the same
  tool, looked like a broken app instead of "someone already removed
  this — refresh."
- Fixed both branches of `delete_tool` to `_render(request, error=...)`
  instead of raising, matching the rest of the app. Updated
  `test_delete_missing_tool_404` (renamed
  `test_delete_missing_tool_shows_friendly_error`) for the new 200 +
  banner behavior. Full suite: 97/97 passing.

## 2026-10-20 (the real cause of the "redirects to Method Not Allowed" reports)

- Root-caused the exact bug behind every "redirects to a 405/404 page"
  report from this user across this whole debugging thread (upload,
  delete — all of it), by getting a full Network-tab capture from their
  real browser: clicking "Удалить" → confirming → the address bar
  landed on `.../tools/echo/delete/` (note the trailing slash) showing
  `{"detail":"Method Not Allowed"}`, with **no visible redirect** before
  it — just one `GET` request, `Referer: .../tools/echo/delete` (no
  slash).
- That `Referer` was the tell: `_render()` in `admin_ui/main.py`
  (shared by `/`, `POST /tools/upload`, `POST /tools/{name}/delete`,
  `POST /config/shared-dir`) always renders `templates/index.html`
  directly, by design, to avoid a redirect that would escape the proxy
  prefix (see the 2026-10-19 "page split" entry). But `index.html`'s
  `self_correct_script` block — the trailing-slash self-correct script,
  meant only for an actual `GET /` — was rendered **unconditionally**
  whenever that template was used, with no awareness that `_render()`
  now also answers POSTs on routes whose own URL (correctly) never ends
  in `/`. On those responses, the script saw `location.pathname` not
  ending in `/` and "corrected" it by issuing its own client-side `GET`
  navigation to that same path + `/` — which 405s, since those are
  POST-only routes. This never showed up as a server-side redirect in
  Network because it's a JS-initiated navigation, not an HTTP 3xx —
  which is exactly why the two previous debugging rounds on this same
  class of symptom (the Starlette `redirect_slashes` fix, and the bare
  404-on-delete fix) didn't catch it: neither one was the actual script
  producing this particular report.
- Fixed by gating the script on a new `self_correct` context flag,
  `_render()` passes in as `request.url.path in ("/", "")` — true only
  for the real root page, false for every POST handler that reuses the
  same template. Mirrors the pattern `login.html` already used for its
  own analogous root-vs-not self-correct gating.
- Added `test_self_correct_script_only_on_actual_root`, directly
  reproducing the reported sequence (GET / has the script; POST
  `/tools/{name}/delete`, `/tools/upload`, `/config/shared-dir` do not).
  Full suite: 98/98 passing.

## 2026-10-20 (mutating actions now redirect back to the Skills page)

- Per explicit request ("ты как бы не редиректишь на основную ... после
  удаления и добавления"), `/tools/upload`, `/tools/{name}/delete`, and
  `/config/shared-dir` now 303-redirect back to `.` (the Skills page)
  with the outcome passed as a `message`/`error` query param, instead of
  rendering `index.html` directly at the POST's own URL with no
  redirect at all (the design from the 2026-10-19 "page split" entry,
  chosen back then specifically to avoid an absolute redirect escaping
  the proxy prefix). That concern is already solved: `_relative_redirect()`
  — proven correct under a real proxy by the `/login` flow, which
  already uses exactly this "bounce back after a successful POST"
  pattern — needed no changes to reuse here. Added `_redirect_to_root()`
  in `admin_ui/main.py`; `index()` now accepts optional `message`/`error`
  query params.
- This also fully closes out the self-correct-script-fires-on-a-POST-
  response bug fixed two entries above: `_render()` (and therefore
  `index.html`'s self-correct script) is now *only* ever reached via
  `GET /`, so there's no longer an inline-rendered non-root page for the
  script to misfire against at all — `self_correct` is unconditionally
  true in practice now, not just correctly gated.
- Updated `test_trailing_slash_never_triggers_a_redirect` (now expects
  303 to a relative target instead of 200) and
  `test_self_correct_script_only_on_actual_root` → renamed
  `test_post_routes_redirect_to_root_with_banner`, verifying the full
  redirect-then-render round trip for all three routes. Verified against
  a real running process with `curl` end-to-end too (not just
  `TestClient`): `POST /tools/nope/delete` → `303` with
  `Location: ../../.?error=...` (percent-encoded Cyrillic) → following
  it lands on `GET /?error=...` → `200` with the decoded error banner
  and the self-correct script both present. (Caught a curl-specific
  false negative along the way: `curl -X POST -L` keeps forcing POST on
  the followed request too, unlike a real browser's spec-correct
  303→GET downgrade — had to drop `-X POST` and let `-d` imply the
  method only on the first request to see the real behavior.) Full
  suite: 98/98 passing.

## 2026-10-20 (SKILL.md no longer needs a YAML frontmatter — name/description now come from the upload form)

- Per explicit request ("никто так не пишет" — a hand-written YAML
  block in a markdown file is unnatural, nobody actually writes doc
  skills that way), dropped the required `---\nname: ...\n
  description: ...\n---` frontmatter from `SKILL.md` entirely. Chose
  "drop it completely, always from the form" over "keep it as an
  optional override" (asked via AskUserQuestion) to avoid two sources of
  truth disagreeing. `SKILL.md` is now just plain markdown — no special
  syntax — used verbatim (`.strip()`-ed) as the tool's body.
- `common/doc_skill.py`'s `validate_doc_skill_archive()` now takes
  `name`/`description` as required keyword arguments instead of parsing
  them from the file; both are validated the same as before (`name`
  against `NAME_RE`, both non-empty). Since there's nowhere left in the
  bundle to persist the admin-supplied `description` once the
  frontmatter is gone, added `write_meta()`/a `.skill_meta.json` sidecar
  file, written at install time and read back by the rewritten
  `validate_doc_skill_dir()` on every re-scan — `name` itself needs no
  sidecar entry since it's already the installed folder's name.
  Excluded the sidecar from the skill's own `files` listing, same as
  `SKILL.md` itself already was. Dropped `parse_skill_md()`, the
  frontmatter regex, and the `pyyaml` dependency entirely — nothing else
  in this project needs YAML.
- `common/registry.py`'s `install_tool()`/`_validate_archive()` gained
  optional `name`/`description` keyword arguments, threaded through to
  the doc-skill validator; a code-skill upload ignores them completely
  (its name/description always come from the function, unchanged).
- Admin UI: `templates/index.html`'s "Add a skill" dialog gained
  **Имя**/**Описание** text fields (with a note that they only matter
  for `SKILL.md` uploads), wired into `upload_tool()` (HTML route) and
  `api_upload_tool()` (JSON API, what a child relay's `curl` call hits)
  as new `Form` fields, both forwarded straight into
  `registry.install_tool(..., name=, description=)`. The child-relay
  branch's `curl_client.post(...)` call also forwards them in its
  `fields=` dict alongside `overwrite`, so a relay's forward to the main
  host carries them too. `templates/docs.html` and `README.md` rewritten
  to match (no more frontmatter example; explains the form fields
  instead).
- Migrated `examples/repo_explainer/SKILL.md` by deleting its
  frontmatter block — the body itself needed no changes. Tests:
  rewrote `test_doc_skill.py` for the new keyword-argument API (dropped
  every frontmatter-parsing test, added name/description-required
  cases and `.skill_meta.json` round-trip/missing/invalid-JSON cases);
  updated `test_registry.py`'s doc-skill helpers and
  `test_server_sync.py`'s `test_doc_skill_round_trip` to pass
  `name=`/`description=` explicitly now that the example's `SKILL.md`
  no longer carries them. Full suite: 100/100 passing.

## 2026-10-20 (automatic git push on every skill install)

- Per explicit request ("при каждом добавлении скилла в указанной папке
  происходил push по обновлениям", credentials "редактироваться только у
  главного админа в настройках"), added `common/git_push.py`:
  `push_shared_dir()` shells out to the real `git` binary
  (`add`/`commit`/`push`) in the shared tools directory, which is assumed
  to already be a git repo with its remote configured (this project
  never runs `git init`/`git remote add` itself). Best-effort — a
  failure never blocks the skill install that triggered it, just returns
  an error string instead of raising.
- Credentials never touch the push command/URL (would leak via `ps aux`
  to every local user on a shared host): added
  `scripts/git_askpass.py`, a `GIT_ASKPASS` script that answers git's
  username/password prompts from its own `GIT_PUSH_USERNAME`/
  `GIT_PUSH_PASSWORD` env vars instead, set only for that one
  `subprocess.run` call.
- `AppConfig` gained `git_push_username`/`git_push_password`
  (`common/config.py`) — configured only by the main admin via the
  existing "⚙" settings dialog, which already only renders its edit form
  on the main host (not a child relay) and already 403s
  `/config/shared-dir` there, so no new access control was needed.
  `save_config()` now `chmod(0o600)`s `config.json` on every write, to
  match `admins.json`'s existing permissions now that this file can hold
  a plaintext secret. The password is never echoed back into the
  rendered form (`_render()` only passes a `git_push_password_set` flag);
  resubmitting the settings form with that field blank keeps the
  previously saved password instead of clearing it.
- Wired the actual push into both places `registry.install_tool()`
  writes to the real shared directory on the main host:
  `upload_tool()`'s local-install branch and `api_upload_tool()` (the
  JSON endpoint a child relay's `curl` call hits). A push failure
  appends a warning to the success banner rather than replacing it;
  `api_upload_tool()`'s response gained a non-breaking `git_push_error`
  field so the relay's own HTML handler can read it back and append the
  same warning to *its* banner too.
- Found and fixed a locale bug while writing tests: this machine's `git`
  reports "нечего коммитить" (Russian), not "nothing to commit", for an
  empty commit — the original implementation string-matched the English
  phrase to distinguish "nothing changed" from a real commit failure,
  which silently broke under a non-English git locale. Fixed by checking
  `git diff --cached --quiet`'s exit code before committing at all,
  instead of parsing `git commit`'s (locale-dependent) output.
- Tests: new `test_git_push.py` (4 tests, using a real local bare repo +
  working clone so `file://` push actually exercises the real
  `git add/commit/push` subprocess chain — happy path, nothing-to-commit,
  not-a-git-repo, and a push-failure case via a broken remote URL).
  `test_admin_api.py` gained `test_set_shared_dir_keeps_git_password_when_left_blank`
  and `test_upload_pushes_to_git_when_configured` (full upload → install
  → push round trip against a real bare repo, asserting the commit
  actually lands there). Full suite: 106/106 passing.

## 2026-10-20 (git push failing with "terminal prompts disabled" on a real host)

- User reported the real push failing with `fatal: could not read
  Username for 'https://...': terminal prompts disabled` — meaning
  `GIT_ASKPASS` wasn't actually answering the prompt. Root cause: a
  system-wide `gitconfig` with its own `credential.helper` already
  configured takes priority over the `GIT_ASKPASS` env var and can
  itself fail (or try to prompt) before our askpass script is ever
  invoked at all — `GIT_TERMINAL_PROMPT=0` then blocks the interactive
  fallback, producing exactly this error with no trace that our script
  ran.
- Fixed by forcing `-c credential.helper=` and `-c
  core.askpass=<script>` directly on the `git push` command line (only
  that step — `add`/`commit` need no credentials). `-c` options always
  win over whatever's in a config file, unlike an env var a helper can
  preempt.
- Confirmed by hand that this genuinely can't be tested end-to-end with
  a local `file://` remote: a credential helper that always fails still
  lets a `file://` push through untouched, since no auth round-trip
  happens for a local path at all. Added
  `test_push_overrides_credential_helper_and_askpass` instead, which
  mocks `subprocess.run` to assert the actual argv of the push call
  (the `-c` flags present, and absent from the `add`/`commit` calls).
  Full suite: 107/107 passing.

## 2026-10-20 (git push: emulate a real terminal instead of askpass/credential.helper)

- The `-c credential.helper=`/`-c core.askpass=` fix from the previous
  entry still didn't work on the user's real corporate Bitbucket Server
  — same `terminal prompts disabled` error, meaning that environment
  doesn't go through git's normal askpass/credential-helper path at
  all. Per explicit request ("насильно эмулировал терминал... вставка
  по тупому логина и пароля после git push"), replaced the whole
  mechanism: `common/git_push.py`'s push step now runs under a real
  pseudo-terminal via `pexpect.spawn`, waits for a prompt matching
  `/username|password|логин|пароль/i`, and types the matching answer —
  i.e. actually emulates a human typing into a terminal, rather than
  any git-specific credential-injection mechanism. This is the one
  approach guaranteed to behave the same way a manual `git push` by a
  human would, regardless of whatever (if anything) is configured.
- Refactored the pty logic into a standalone
  `_run_under_pty_answering_credentials(argv, cwd, username, password)`
  so it's testable against a fake prompting script, not just real git —
  deleted the now-unused `scripts/git_askpass.py` and the `-c`
  overrides entirely. Added `pexpect>=4.9` to `requirements.txt`.
- Tests: rewrote `test_git_push.py`'s now-invalid
  `test_push_overrides_credential_helper_and_askpass` (that mechanism
  no longer exists) into `test_run_under_pty_answers_real_username_and_password_prompts`,
  which spawns a tiny fake script that genuinely prints "Username for
  ...: "/"Password for ...: " and reads replies via `input()` — this
  exercises the actual pty read/write loop, which a local `file://` git
  remote can't (it never prompts for credentials at all, confirmed by
  hand in the previous entry). Added
  `test_run_under_pty_reports_nonzero_exit` for the failure path. Noted
  (not fixed, see PLAN.md) a `DeprecationWarning` about `forkpty()` in a
  multi-threaded process — accepted as a known tradeoff since skill
  installs aren't usually concurrent in practice. Full suite: 109/109
  passing.

## 2026-10-20 (git push on delete too, and doc-skill description editing)

- Per explicit request ("удаление данных также должно вызывать это
  дело"), `delete_tool`/`api_delete_tool` now trigger the same git push
  as installing a skill, with a "Remove skill '<name>'" commit message.
  Generalized `_maybe_git_push()`'s third parameter from a bare skill
  name (which only ever built an "Add skill" message) to the full
  commit message, so both call sites could share it.
- Per explicit request ("сделай редактирование... описания") and a
  follow-up decision (asked via AskUserQuestion: doc skills only, since
  a code skill's description is its function's own docstring, edited by
  editing the `.py` file directly — not something this feature should
  duplicate a second way to change), added
  `registry.update_doc_skill_description()`: rewrites a doc skill's
  `.skill_meta.json` sidecar in place, leaving `SKILL.md`'s body and
  every other file untouched; rejects a code-skill name with a clear
  `ToolValidationError` instead of silently doing nothing.
- Wired a new `POST /tools/{name}/edit-description` (HTML, follows the
  same main-host/child-relay-forwarding pattern as upload/delete) and
  `POST /api/tools/{name}/edit-description` (JSON, what the relay's
  `curl` call hits), both triggering the same git push on success.
  `templates/index.html`: doc skills (kind `"doc"` only) get a new
  "Редактировать" button next to "Удалить", opening a shared dialog
  (`editDescriptionDialog`) whose form action/textarea value get set via
  a small inline `openEditDescription(name, description)` JS function —
  one dialog reused for every row rather than one per skill.
  `templates/base.html` gained `textarea`/`input[type=password]` styling
  (the latter existed in the markup already but had no CSS) and a new
  `.edit-btn` class matching `.remove-btn`'s shape in blue instead of
  red.
- Tests: `test_registry.py` gained 4 tests for
  `update_doc_skill_description` (happy path incl. body/files surviving
  untouched, missing-tool, empty-description, rejects-code-skill).
  `test_admin_api.py` gained `test_edit_doc_skill_description`,
  `test_edit_description_rejects_code_skill`,
  `test_edit_description_missing_skill_shows_error`, and
  `test_delete_pushes_to_git_when_configured` (full delete → push round
  trip against a real bare repo, mirroring the existing upload-pushes
  test). Full suite: 117/117 passing.

## 2026-10-20 (MCP_TOOLS_READ_ONLY startup flag)

- Per explicit request ("при запуске... флаг, который не даст
  возможности добавления и редактирования"), with a follow-up decision
  (asked via AskUserQuestion: should "Удалить" also be blocked — yes),
  added `MCP_TOOLS_READ_ONLY=1`: a startup-only env var (matching the
  existing `MCP_TOOLS_MAIN_HOST`/`MCP_TOOLS_INSECURE_TLS` convention,
  not a new persisted `AppConfig` field, since this is a deployment-mode
  decision rather than something changed without restarting) that
  disables install/overwrite, delete, and description editing, leaving
  only viewing the Skills list.
- Checked at the top of all six mutating routes: the three HTML ones
  (`upload_tool`, `delete_tool`, `edit_doc_skill_description`, each
  redirecting to the root with `READ_ONLY_MESSAGE` as the error) and the
  three JSON API ones a child relay's `curl` call hits
  (`api_upload_tool`, `api_delete_tool`, `api_edit_doc_skill_description`,
  each returning `403`). Checking the JSON API matters just as much as
  the HTML side: a read-only *main* host blocks relay-forwarded
  mutations automatically, with no changes needed on the relay's side at
  all.
- `templates/index.html`: the "+" add-skill button, "Удалить", and
  "Редактировать" are all hidden from the rendered page in this mode
  (via a new `read_only` context flag threaded through `_render()`), not
  just rejected server-side — plus a banner at the top of the Skills
  page explicitly stating the admin UI is read-only. `/config/shared-dir`
  (shared directory path / git-push credentials) is deliberately
  unaffected — the request was specifically about protecting skills, not
  app configuration.
- Tests: `test_admin_api.py` gained a `read_only_client` fixture
  (reloads `admin_ui.main` with `MCP_TOOLS_READ_ONLY=1` set before
  import, since the flag is read once at module level) and 5 new tests
  covering each blocked HTML action, the hidden buttons, and the JSON
  API's `403`s. Full suite: 122/122 passing.

## 2026-10-20 (fixed "Редактировать" button doing nothing)

- User reported clicking "Редактировать" did nothing. Root cause: the
  button's `onclick` was built as
  `onclick="openEditDescription('{{ t.name }}', {{ t.description | tojson }})"`
  — `tojson` produces a JSON string (double-quote delimited) but does
  **not** HTML-escape it for embedding inside a double-quoted HTML
  attribute. A description containing any literal `"` (or really any
  description at all, since `tojson` always wraps the string in `"..."`)
  closed the `onclick="..."` attribute early, truncating the handler —
  confirmed by rendering the real page and inspecting the actual HTML:
  `onclick="openEditDescription('test-skill', "Use this skill..."` was
  cut off mid-attribute.
- Fixed by moving the data out of the inline handler entirely:
  `data-name`/`data-description` attributes (plain Jinja interpolation,
  which *does* correctly HTML-escape for attribute context — verified
  by hand with a description containing both `'` and `"`, rendered as
  `&#39;`/`&#34;`) plus `onclick="openEditDescription(this)"` reading
  `this.dataset.name`/`this.dataset.description` in JS instead of
  receiving them as literal arguments baked into the HTML.
- Added `test_edit_button_data_attributes_survive_quotes_in_description`
  reproducing the exact failure mode (a description with both `'` and
  `"`) and asserting the button's markup stays well-formed. Full suite:
  123/123 passing.

## 2026-10-22 (write a doc skill directly in the admin UI; archives can be a zipped folder)

- Per explicit request ("сделай возможность прямо в админке добавлять
  тул и писать текст MD для него"), added a way to create a doc skill
  straight from text typed into the admin UI — no archive at all.
  `common/doc_skill.py`: promoted `_check_name_and_description()` to
  public `check_name_and_description()`, added `create_doc_skill_dir()`
  (writes `SKILL.md` + the `.skill_meta.json` sidecar directly to the
  destination folder). `common/registry.py`: new `create_doc_skill()`
  calling that, with the same existence/overwrite check every other
  install path uses. Wired through `POST /tools/create-doc-skill` (HTML)
  and `POST /api/tools/create-doc-skill` (JSON, what a child relay's
  `curl` call hits), both gated by `READ_ONLY` and triggering the same
  git push as `/tools/upload` on success. `templates/index.html` gained
  a "✎ Написать навык" button next to "+", opening a wide dialog
  (`dialog.wide`, new CSS in `base.html`) with Имя/Описание/a Markdown
  textarea. Deliberately doesn't support attaching extra files
  (`references/*`) — that's what the archive upload remains for.
- Per a second explicit request in the same message ("формат архива
  поправь, чтобы внутри архива могла быть папка с содержимым"), archive
  uploads now tolerate being a zip *of a folder* (e.g.
  `myskill/SKILL.md` instead of `SKILL.md` at the root — the normal
  result of `zip -r x.zip myskill/` or a file manager's "Compress"
  action), not just a zip of the skill's files directly. New
  `registry._common_root_dir()` detects a single shared top-level
  folder wrapping *every* file in the archive (if any file already sits
  at the true root, nothing is stripped — the existing flat shape stays
  untouched) and `registry._normalized_archive()` (a context manager)
  yields a temporary copy with that folder's prefix removed from every
  member name. `install_tool()` now does detection, validation, *and*
  the final `extractall()` inside that one normalized context, so
  there's exactly one normalization pass applied consistently — no
  separate, easy-to-forget strip at extraction time.
- Tests: `test_registry.py` gained `create_doc_skill` tests (happy path,
  duplicate without/with overwrite, invalid name, empty body) and
  wrapping-folder tests for both bundle kinds plus a regression test
  that an already-flat archive (a root-level file) is left alone.
  `test_admin_api.py` gained the HTML route's happy path, duplicate
  error, read-only-mode block (both HTML and JSON API), and an
  upload-with-wrapping-folder case. `test_remote_mode.py` gained a
  child-relay round trip for the new create route. Full suite: 137/137
  passing.

## 2026-10-22 (skill groups + search, visible to the agent too)

- Per explicit request ("разделять на подгруппы, чтобы можно было по
  группам искать релевантное"), with two follow-up decisions (asked via
  AskUserQuestion: groups for both code and doc skills; group visible to
  the agent too, "чтобы он не перегружал контекст избыточной
  информацией"), added an optional `group` field to both bundle kinds.
  Code skills: an optional module-level `__group__ = "..."` string
  literal, same AST-inspection convention `__version__` already used —
  generalized `manifest.py`'s `_extract_version()` into
  `_extract_module_string(tree, var_name, default)` so both reuse one
  function instead of two near-duplicates. Doc skills: a `group` form
  field alongside name/description, persisted in `.skill_meta.json`
  (now `{"description", "group"}` — `validate_doc_skill_dir` defaults a
  missing `"group"` key to `""` so skills installed before this feature
  keep loading).
- The agent-visible effect: `mcp_server/server.py`'s `sync_tools()` now
  registers a tool's description as `f"[{group}] {description}"` when
  `group` is set, plain `description` otherwise — a connecting agent
  sees the group right in `tools/list` and can judge relevance without
  spending context reading full descriptions of tools in clearly
  irrelevant categories. Considered and rejected a heavier two-tier
  "group meta-tool" discovery flow (see PLAN.md) — MCP has no native
  category concept, and collapsing every code skill behind one generic
  dispatcher would've thrown away the per-function, type-hint-derived
  input schema this project has relied on from the start.
- Admin UI: `registry.update_doc_skill_description()` renamed to
  `update_doc_skill_metadata()` (now takes `description`/`group`
  together); `install_tool()`/`create_doc_skill()` gained a `group`
  parameter. `templates/index.html`: a "Группа" field in the upload
  form, the "Написать навык" form, and the edit dialog (extended to
  edit group alongside description); a green group badge per skill row;
  and a search box above the skill list (`#skillSearch`) that filters
  rows client-side via a `data-search` attribute (lowercased name +
  description + group) and a plain `oninput` handler — no new route, no
  server round trip, matching this project's existing minimal-JS
  approach everywhere else in the UI.
- Tests: `test_manifest.py` (`__group__` picked up / defaults to empty),
  `test_doc_skill.py` (group round-trips through archive validation and
  `.skill_meta.json`, including a legacy-meta-file-without-group
  compatibility case), `test_registry.py` (`create_doc_skill`/
  `update_doc_skill_metadata` with a group), `test_server_sync.py` (the
  `[group]` prefix appears when set, doesn't when it isn't). Updated
  every existing caller of the renamed
  `update_doc_skill_description`/the now-keyword-only `write_meta()`.
  Full suite: 145/145 passing.

## 2026-10-22 (real group-based context reduction: groups as entities, dispatcher tools)

- Follow-up to the above: user pointed out the `[group]` prefix didn't
  actually shrink anything agents see ("Ведь пока что у нас нет
  уменьшения контекста для агентов?") and asked how to even create/
  describe a group. Two more AskUserQuestion decisions: both code and
  doc skills get the *real* hiding treatment (accepting the loss of
  per-skill JSON schema for grouped code skills); skills without a
  group keep registering as standalone tools directly, unchanged.
- New `common/groups.py`: `Group(name, description)` stored in
  `<shared_dir>/groups.json`, with `create_group`/`delete_group`/
  `list_groups`/`get_group` — same flat-JSON-store shape as
  `common/users.py`. Description is required at creation time since
  it becomes the group's own MCP tool description.
- `mcp_server/server.py`'s `sync_tools()` rewritten: manifests whose
  `group` matches a real `Group` are no longer registered individually
  at all. Instead one dispatcher tool per group (`_make_group_tool`) is
  registered, named after the group, described with the group's own
  description. Called with no `skill` arg it lists the group's skill
  names plus a parameter hint per skill (`_param_hint()`, via
  `inspect.signature()` on the loaded entrypoint); called with
  `skill=<name>, arguments={...}` it dispatches to that skill. Manifests
  with no group, or a group string matching no real `Group`, fall back
  unchanged to standalone registration (tested:
  `test_sync_falls_back_to_standalone_for_unrecognized_group`).
- Admin UI: new "Группы" tab (`templates/groups.html`) — list with
  delete, "+" dialog to create (name + description + overwrite). Wired
  through both HTML routes (`GET/POST /groups`, `POST
  /groups/{name}/delete`) and JSON API routes (`GET/POST /api/groups`,
  `POST /api/groups/{name}/delete`) for child-relay forwarding — Groups
  behaves like Skills here (works through a relay), not like Users/Docs
  (main-host-only). `base.html`'s nav restructured so "Навыки"/"Группы"
  show on a relay too. Both create/delete call `_maybe_git_push` like
  every other mutation, since `groups.json` lives in `shared_dir`.
  `index.html`'s three free-text "Группа" inputs (upload form,
  create-doc-skill form, edit dialog) became `<select>`s populated from
  `group_store.list_groups()`/`_remote_list_groups()`, so an admin picks
  a real group instead of guessing a spelling.
- Tests: `test_groups.py` (new, 10 tests, full CRUD). Two new
  `test_server_sync.py` cases for the dispatcher-vs-fallback split. Five
  new `test_admin_api.py` cases (create/list/delete round trip,
  duplicate-without-overwrite error, read-only gating for both HTML and
  JSON routes, nav link presence). Two new `test_remote_mode.py` cases
  (child-relay group round trip, relay Groups page has no Users/Docs
  links). Full suite: 164/164 passing.

## 2026-10-22 (remove skill versioning entirely)

- Per explicit request ("Это версионирование все равно не работает") —
  `version` never gated or diffed anything; `overwrite=True` already
  replaces a skill in place with no history, so the number was pure
  decoration. Removed the `version` field from `Manifest` and
  `DocSkillManifest`, the `__version__` module-level AST extraction and
  `DEFAULT_VERSION` constant in `manifest.py` (`_extract_module_string`
  itself stays — still used for `__group__`), the `v{version}` in
  admin-UI success messages and JSON API responses, the `v%s` in
  `sync_tools()`'s log line, the `.skill-version` badge/CSS on the
  Skills page, and the "Версия" bullet in the in-app docs tab.
- Tests: dropped `test_custom_version_picked_up` and the `version`
  assertions in `test_manifest.py`/`test_doc_skill.py`. Full suite:
  163/163 passing.

## 2026-10-22 (edit a group's description; hover tooltip for truncated skill descriptions)

- Per explicit request ("Почему нет возможности редактировать описание в
  группе") — the backend already supported this via
  `create_group(..., overwrite=True)`, only the UI was missing it.
  `groups.html` gained a "Редактировать" button per row
  (`data-name`/`data-description`) that reuses the existing
  `addGroupDialog`/`POST /groups` form: `openEditGroup()` pre-fills
  name/description, makes name read-only (it's the group's identity),
  hides+force-checks the overwrite checkbox, relabels the submit button.
  `openAddGroup()` (wired to both "+" buttons) resets back to create
  mode. No new route — same `POST /groups` handler.
- Per explicit request ("При наведении на скилл, я хочу чтобы всплывала
  карточка с описанием, названием и т.д.") — the Skills page truncates
  each description to one line with an ellipsis, so long descriptions
  were only readable via the edit dialog. Added a pure-CSS hover
  tooltip: each `.skill-row` (`position: relative`) now carries a
  sibling `.skill-tooltip` div (name, kind, group, full untruncated
  description) shown via `.skill-row:hover .skill-tooltip` — no JS, no
  new template context.
- Tests: `test_groups_page_edit_button_and_overwrite_updates_description`
  (new) checks the edit button renders with the right `data-*` and that
  posting with `overwrite=true` actually updates the stored description.
  Full suite: 164/164 passing.

## 2026-10-27 (meta-skill: a skill that creates other skills)

- Per explicit request ("навык, который позволит делать навыки по
  формату для нас") — added `examples/create_skill/tool.py`, a code
  skill whose single function `create_skill(kind, name="",
  description="", body="", source="", group="", overwrite=False)` lets
  an agent install a brand-new skill (doc or code) directly into the
  shared directory, no admin UI involved. `kind="doc"` just calls
  `registry.create_doc_skill()`. `kind="code"` stages `source` as a
  `.py` file in a temp dir under `tools_dir`, validates it with the
  existing `manifest.validate_dir()`, then moves it into place at the
  validated `manifest.name` — reuses the project's existing validation
  instead of writing a new one, and never leaves a half-written skill
  dir behind on a validation failure. Resolves the shared directory via
  `common.config.load_config().shared_dir_path`, same call the admin UI
  and the MCP server's own `main()` already use, so it always targets
  whatever directory the running server is actually syncing from.
- Verified live end-to-end: installed the skill into the real
  `shared_dir`, confirmed it appears in `tools/list` after the next 5s
  sync, then called it (as an actual MCP tool from this session) with
  both `kind="doc"` and `kind="code"` — both new skills showed up
  correctly (the code skill's `int` parameter got the right inferred
  schema) — then removed the two throwaway test skills, keeping
  `create_skill` itself installed.
- No new backend code needed in `jupyter_mcp_tools` itself — this is
  purely an example skill built entirely out of the already-public
  `registry`/`manifest`/`config` API, same category as
  `examples/echo_tool`/`setup_files_tool`/`repo_explainer`. Documented in
  README.md under "A skill that creates other skills".

## 2026-10-27 (download an installed skill back as a .zip)

- Per explicit request ("сделать возможность скачивать пак с навыком") —
  the reverse of `/tools/upload`. `registry.build_download_archive()`
  zips an installed skill's folder with content at the archive root (no
  wrapping folder), skipping `.skill_meta.json` for a doc skill (that's
  this app's own bookkeeping, regenerated from the form on re-upload
  anyway) — the result round-trips straight back through
  `install_tool()` unchanged.
- `curl_client.get()`/`post()` both capture the response body as text,
  which would corrupt a binary zip when a child relay proxies a
  download — added `curl_client.download()`, which uses `curl -o <path>
  -w "%{http_code}"` to write the body to a file and only read the
  status code off stdout.
- Wired as `GET /tools/{name}/download` (HTML, session-gated,
  `FileResponse` + a `BackgroundTask` cleaning up the temp zip) and `GET
  /api/tools/{name}/download` (JSON API — what a child relay's `curl`
  call hits). Deliberately not gated by `MCP_TOOLS_READ_ONLY` — it's a
  read, not a mutation. Added a plain "Скачать" link per skill row in
  `index.html` (no JS needed).
- Tests: `test_build_download_archive_round_trips_code_skill`,
  `..._excludes_meta_file_for_doc_skill`, `..._round_trips_through_reupload`,
  `..._missing_tool_raises` in `test_registry.py`;
  `test_download_installed_skill_returns_zip`,
  `test_download_missing_skill_returns_404`,
  `test_download_allowed_in_read_only_mode` in `test_admin_api.py`;
  `test_child_relay_download_round_trip`,
  `test_child_relay_download_missing_skill_returns_404` in
  `test_remote_mode.py` (real subprocess + real `curl`, same as every
  other relay test). Full suite: 173/173 passing. Verified the route
  itself is wired on the real running admin UI (unauthenticated request
  correctly 303s to `/login` rather than 404ing).

## 2026-10-27 (fix: skill hover tooltip was clipped by the card's edges)

- Per explicit request ("При наведении на скилл его всплывашка
  скрывается за границами блока") — `.card` had `overflow: hidden`
  (to stop rows' square corners poking out past the card's own rounded
  ones), which also clipped the `.skill-tooltip` div since it's a
  descendant of `.skill-row`, a descendant of `.card`. Removed `overflow:
  hidden` from `.card` and rounded the first/last child's own corners
  instead (`.card > :first-child`/`:last-child`) — rows have no
  background of their own distinct from the card's, so nothing was
  actually relying on that clip. Pure CSS change in `base.html`; full
  suite still 173/173 (no test coverage for this — purely visual).

## 2026-10-27 (make the MCP server's `instructions` admin-editable)

- Per explicit request, following a discussion about agents in this
  system being sandboxed from running most scripts directly but able to
  run skills shared through this MCP server — the fix is a standing
  hint baked into the one MCP field every connecting agent gets
  automatically: `instructions`. Previously hardcoded in
  `mcp_server/server.py`'s `build_app()`; now `common/config.py`'s
  `AppConfig.instructions` (persisted, editable) plus a
  `DEFAULT_INSTRUCTIONS` constant (extended with the sandboxing hint:
  "if blocked, ask an admin to install the script as a skill and call
  it as a tool instead") and an `effective_instructions` property.
- `FastMCP.instructions` has no setter — mutated the underlying
  `app._mcp_server.instructions` directly instead (plain attribute,
  re-read fresh by `create_initialization_options()` per session, not
  baked in once), added to `sync_tools()`'s per-tick work so an edit
  reaches new connections without restarting the server.
- Found by testing against the real running server (not just unit
  tests): there's a one-connection lag — a session's `instructions` is
  snapshotted *before* its own lifespan (which calls `sync_tools`) runs,
  so a connection in flight exactly when you save won't see the change;
  the one after it will. Documented honestly in the code comment and
  the admin UI copy instead of claiming instant effect.
- Added a `<textarea name="instructions">` to the existing "⚙" dialog
  in `index.html`, pre-filled with the effective (stored-or-default)
  value; clearing it resets to the default rather than becoming stuck
  empty. Wired through `/config/shared-dir`, main-host-only like the
  git-push/Bitbucket fields already there.
- Had to add HOME/`MCP_TOOLS_CONFIG_PATH` isolation to the `shared_dir`
  fixture in `conftest.py` — `sync_tools`/`build_app` now call
  `load_config()`, which `test_server_sync.py` never needed to isolate
  before this feature touched it.
- Tests: `test_set_shared_dir_saves_custom_instructions`,
  `test_address_dialog_shows_default_instructions_when_unset`,
  `test_clearing_instructions_resets_to_default` in `test_admin_api.py`;
  `test_build_app_defaults_to_default_instructions`,
  `test_sync_picks_up_custom_instructions_live` in
  `test_server_sync.py`. Full suite: 178/178 passing. Verified live
  against the real running MCP server: confirmed the new default text
  (with the sandboxing hint) in a real `initialize` response, then
  edited `instructions` in the real config.json and confirmed it
  surfaced without restarting the server (after the one-connection lag
  described above), then reset the real config back to the default.

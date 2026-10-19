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

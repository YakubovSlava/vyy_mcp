"""Admin web UI for uploading and sharing MCP tool archives.

Two ways to run this app, selected by the MCP_TOOLS_MAIN_HOST env var:

- **Main host** (MCP_TOOLS_MAIN_HOST unset): this is the real thing — it
  reads/writes the shared tools directory directly. Every page requires
  signing in through `/login` (a normal HTML form, not the browser's
  native Basic Auth popup), checked against the admin accounts the main
  admin creates with `jupyter-mcp-tools-admin-users` (see
  `common/auth.py`); the session is a signed cookie (`common/session.py`).
  The JSON API under `/api/...` is separate and still uses HTTP Basic
  Auth — that's what child relays' `curl` calls authenticate with.
- **Child-admin relay** (MCP_TOOLS_MAIN_HOST set, normally via
  `scripts/run_child_admin.sh`): this instance has no local shared
  directory of its own. Every page view / upload / delete is forwarded to
  the main host's JSON API over `curl` (see `common/curl_client.py`),
  authenticated with MCP_TOOLS_ADMIN_USER / MCP_TOOLS_ADMIN_PASSWORD — the
  credentials the main admin handed this admin. There's no local login on
  this side: reaching this process at all already means the child admin
  is logged into their own Jupyter server, which is the trust boundary
  here.

Run with:

    uvicorn jupyter_mcp_tools.admin_ui.main:app --host 0.0.0.0 --port 8766
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from ..common import auth, curl_client, git_push, registry, session, users
from ..common import builtin_skills
from ..common import groups as group_store
from ..common import repo_files
from ..common.config import AppConfig, load_config, save_config
from ..common.manifest import ToolValidationError
from ..common.registry import ToolAlreadyExistsError, ToolNotFoundError

TEMPLATES_DIR = Path(__file__).parent / "templates"

def _normalize_main_host(raw: str) -> str:
    """Turn whatever the admin typed into a usable base URL, or fail loudly.

    The old code only stripped a trailing slash, so a value that was empty,
    surrounded by quotes (copy-pasted from the README), missing its scheme, or
    carrying a trailing space went straight into `curl`. curl then failed with
    "URL using bad/illegal format or missing URL" at the first request — long
    after startup, with no hint about which input was wrong. Failing at import
    time with the offending value is far cheaper to diagnose.

    An *unset* variable is not an error: that's the main host, which is why
    this returns "" for it.
    """
    if raw is None or raw == "":
        # Main host: no relay configured.
        return ""

    value = raw.strip().strip("'\"").strip().rstrip("/")
    if not value:
        raise RuntimeError(
            "MCP_TOOLS_MAIN_HOST задан, но пуст после очистки — укажите адрес "
            "основного хоста, например https://main.example.org:8766"
        )
    if not value.startswith(("http://", "https://")):
        raise RuntimeError(
            f"MCP_TOOLS_MAIN_HOST должен начинаться с http:// или https://, "
            f"получено: {raw!r}"
        )
    if " " in value or "\t" in value:
        raise RuntimeError(f"MCP_TOOLS_MAIN_HOST содержит пробел: {raw!r}")
    return value


MAIN_HOST = _normalize_main_host(os.environ.get("MCP_TOOLS_MAIN_HOST"))
CHILD_USER = os.environ.get("MCP_TOOLS_ADMIN_USER", "")
CHILD_PASSWORD = os.environ.get("MCP_TOOLS_ADMIN_PASSWORD", "")
INSECURE_TLS = os.environ.get("MCP_TOOLS_INSECURE_TLS") == "1"
# Locks down every mutating skill action (install/overwrite, delete,
# edit-description) on *this* process — set at startup, not toggleable
# from the UI itself, per explicit request. Checked at the top of every
# mutating route (HTML and JSON API alike), so it applies the same way
# whether this is the main host or a child relay forwarding to one.
READ_ONLY = os.environ.get("MCP_TOOLS_READ_ONLY") == "1"
READ_ONLY_MESSAGE = "Админка запущена в режиме «только просмотр» — изменения навыков отключены."

# Starlette's own `redirect_slashes` (on by default) would otherwise 307
# any request whose path doesn't exactly match a route's trailing slash
# to `request.url.replace(path=...)` — an *absolute* Location
# (scheme+host+path) built with zero awareness of whatever proxy prefix
# fronts us (jupyter-server-proxy, a real Hub, ...). That's exactly the
# kind of proxy-unaware redirect this app has had to avoid everywhere
# else (see _relative_redirect below) — observed in production as a
# proxied `POST .../tools/upload/` getting redirected straight to
# `<domain-root>/tools/upload`, dropping the whole `/user/<name>/
# proxy/<port>` prefix. Disabling it here and normalizing the slash
# ourselves (see StripTrailingSlashMiddleware) means no redirect is ever
# needed for this at all.
app = FastAPI(
    title="MJApp (Lab edition)",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    redirect_slashes=False,
)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _filesize(value: int) -> str:
    """Bytes to a short human string for the Files page."""
    size = float(value or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


templates.env.filters["filesize"] = _filesize


class StripTrailingSlashMiddleware:
    """Normalizes away a trailing slash on any non-root path before
    routing, so a proxy that happens to forward one never triggers
    Starlette's disabled-but-still-worth-avoiding redirect behavior
    (see the comment on `redirect_slashes` above) or a plain 404."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"]
            if len(path) > 1 and path.endswith("/"):
                scope = {**scope, "path": path.rstrip("/")}
        await self.app(scope, receive, send)


app.add_middleware(StripTrailingSlashMiddleware)

_basic_security = HTTPBasic(auto_error=False)


class NotAuthenticated(Exception):
    pass


def _relative_redirect(request: Request, target: str) -> str:
    """Build a Location value relative to the CURRENT request path, so it
    resolves correctly under any external mount prefix without needing
    any proxy in front of us to rewrite it.

    `target` is a path with no leading slash (e.g. "login", "." for the
    app root). See PLAN.md for why this replaced an earlier
    X-Forwarded-Prefix-based approach: jupyter-server-proxy happens to
    rewrite Location headers on redirects, but JupyterHub's own proxy
    (configurable-http-proxy) is a dumb passthrough and does not — a
    plain "Location: /login" there resolves against the *domain* root in
    the browser, escaping the Hub's /user/<name>/... prefix entirely.
    """
    segments = request.url.path.strip("/").split("/")
    up = max(len(segments) - 1, 0)
    return "../" * up + target


def _redirect_to(request: Request, target: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    """After a mutating POST, send the browser back to `target` (a plain
    relative path with no leading `/`, e.g. "." for the Skills page or
    "groups" for the Groups page) instead of rendering a page inline at
    the POST's own URL — passing the outcome along as a query param for
    that page's own GET route to show as a banner.

    Per explicit request: previously these routes rendered the page
    directly without redirecting at all (to avoid an *absolute* redirect
    escaping the proxy prefix — see PLAN.md). That's no longer needed:
    `_relative_redirect()` below is already proven correct under a real
    proxy (used by the login flow), so there's no reason not to use it
    here too and land the user back on the actual list page.
    """
    params = {k: v for k, v in {"message": message, "error": error}.items() if v}
    full_target = target if not params else f"{target}?{urlencode(params)}"
    return RedirectResponse(_relative_redirect(request, full_target), status_code=303)


def _redirect_to_root(request: Request, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    return _redirect_to(request, ".", message=message, error=error)


def _redirect_to_groups(request: Request, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    return _redirect_to(request, "groups", message=message, error=error)


@app.exception_handler(NotAuthenticated)
def _redirect_to_login(request: Request, exc: NotAuthenticated):
    if request.url.path in ("/", ""):
        # The root path has no segment of its own to anchor a relative
        # reference on: whether "login" resolves correctly depends on
        # whether the browser's current URL happens to already end in
        # "/" — which varies by how the user arrived (typed URL,
        # bookmark, a Hub's own landing redirect, ...). Every *other*
        # route has a real trailing segment ("users", "tools/upload",
        # ...), so a relative "../login" unambiguously lands one level
        # up from it regardless of trailing slash — this is the one
        # case that doesn't. Serve the login page directly (status 200,
        # no redirect at all) so its own trailing-slash self-correct
        # script (see templates/base.html) can fix that up client-side
        # before the form is ever shown or submitted.
        return templates.TemplateResponse(request, "login.html", {"error": None, "self_correct": True})
    return RedirectResponse(_relative_redirect(request, "login"), status_code=303)


def require_admin_page(request: Request) -> str | None:
    """Gate the browser-facing HTML routes.

    Main host: requires a valid session cookie from `/login`, otherwise
    redirects there. Child relay: no-op — the child admin's own Jupyter
    login is the trust boundary on that side, and every action is
    re-authenticated against the main host via Basic Auth anyway.
    """
    if MAIN_HOST:
        return None
    token = request.cookies.get(session.COOKIE_NAME)
    username = session.verify_token(token) if token else None
    if username is None:
        raise NotAuthenticated()
    return username


def require_admin_api(credentials: HTTPBasicCredentials | None = Depends(_basic_security)) -> str | None:
    """Gate the JSON API (`/api/...`) with HTTP Basic Auth.

    This is what a child relay's `curl` calls authenticate with — kept
    separate from the HTML routes' cookie-session login so machine
    callers never need to deal with a login *page*.
    """
    if credentials is None or not auth.verify_admin(credentials.username, credentials.password):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


BUILTIN_SKILL_MESSAGE = (
    "Это встроенный навык — его нельзя изменить или удалить. "
    "Он восстанавливается автоматически и нужен агенту как есть."
)


def _reject_builtin(name: str, request: Request):
    """Refuse to edit/delete a built-in skill.

    Returning a redirect rather than raising keeps the HTML routes'
    convention (a banner in the page body), and the same check runs on the
    JSON API so a child relay can't slip past it either.
    """
    if builtin_skills.is_builtin(name):
        return _redirect_to_root(request, error=BUILTIN_SKILL_MESSAGE)
    return None


def _main_host_error(exc: Exception) -> str:
    """Turn a forwarding failure into something an admin can act on.

    The raw curl text for a bad address is "URL using bad/illegal format or
    missing URL" — it names neither the setting nor the value, which is exactly
    the dead end a child admin hit. Anything that smells like a malformed URL
    gets the configured value and the expected shape attached.
    """
    message = str(exc)
    lowered = message.lower()
    # curl's own wording for a mangled address varies by version and locale:
    # "URL using bad/illegal format or missing URL", "URL rejected: Malformed
    # input to a URL function", "Port number was not a decimal number".
    # Matching on the URL keyword plus any of those tells is deliberately
    # loose, since the alternative is showing the raw string to an admin who
    # has no way to tell which setting produced it.
    url_trouble = "url" in lowered and (
        "rejected" in lowered
        or "malformed" in lowered
        or "format" in lowered
        or "missing" in lowered
        or "port number" in lowered
        or "bad/illegal" in lowered
    )
    if url_trouble:
        return (
            f"Адрес основного хоста выглядит некорректно: {MAIN_HOST!r}. "
            "Ожидается вид https://main.example.org:8766 — со схемой http:// или https:// "
            f"и без пробелов. Перезапустите scripts/run_child_admin.sh с исправленным адресом. "
            f"(curl: {message})"
        )
    return f"Could not reach main host {MAIN_HOST!r}: {message}"


def _remote_list_files() -> tuple[list[dict], str | None]:
    try:
        resp = curl_client.get(f"{MAIN_HOST}/api/files", CHILD_USER, CHILD_PASSWORD, insecure=INSECURE_TLS)
    except curl_client.CurlError as exc:
        return [], _main_host_error(exc)
    if resp.status == 401:
        return [], "Main host rejected these credentials (401)."
    if resp.status != 200:
        return [], f"Main host returned HTTP {resp.status}."
    try:
        return json.loads(resp.text), None
    except json.JSONDecodeError:
        return [], "Main host returned a response that wasn't valid JSON."


def _forward_post(path: str, fields: dict[str, str]):
    """POST to the main host's JSON API and hand its answer straight back.

    Status codes are preserved rather than collapsed to 200/500: a child
    admin has to see "already exists" (409/400) differently from "the host
    is down", and the page renders the error text from the body.
    """
    try:
        resp = curl_client.post(
            f"{MAIN_HOST}{path}", CHILD_USER, CHILD_PASSWORD, fields=fields, insecure=INSECURE_TLS
        )
    except curl_client.CurlError as exc:
        return JSONResponse(status_code=502, content={"error": f"Could not reach main host: {exc}"})
    try:
        payload = json.loads(resp.text) if resp.text.strip() else {}
    except json.JSONDecodeError:
        payload = {"error": resp.text}
    return JSONResponse(status_code=resp.status, content=payload)


def _forward_upload(api_path: str, archive, fields: dict[str, str]):
    """Multipart upload through the relay.

    Spooled to a temp file first: `curl` needs a real path (`-F name=@file`),
    and the child must not hold the whole archive in memory just to forward
    it.
    """
    import tempfile as _tempfile

    with _tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / "upload.zip"
        archive_path.write_bytes(archive.file.read())
        try:
            resp = curl_client.post(
                f"{MAIN_HOST}{api_path}",
                CHILD_USER,
                CHILD_PASSWORD,
                fields=fields,
                files={"archive": str(archive_path)},
                insecure=INSECURE_TLS,
            )
        except curl_client.CurlError as exc:
            return JSONResponse(status_code=502, content={"error": f"Could not reach main host: {exc}"})
    try:
        payload = json.loads(resp.text) if resp.text.strip() else {}
    except json.JSONDecodeError:
        payload = {"error": resp.text}
    return JSONResponse(status_code=resp.status, content=payload)


def _forward_download(api_path: str, params: dict[str, str], *, filename: str):
    """Stream a binary file from the main host to the child's browser."""
    import tempfile as _tempfile

    if not filename or "/" in filename or "\\" in filename:
        filename = "download.bin"
    with _tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "payload.bin"
        try:
            status = curl_client.download(
                f"{MAIN_HOST}{api_path}?{urlencode(params)}",
                CHILD_USER,
                CHILD_PASSWORD,
                dest,
                insecure=INSECURE_TLS,
            )
        except curl_client.CurlError as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach main host: {exc}") from exc
        if status != 200:
            raise HTTPException(status_code=status, detail=dest.read_text(errors="replace"))
        payload = dest.read_bytes()

    from starlette.responses import Response

    return Response(
        payload,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _remote_list_tools() -> tuple[list[dict], str | None]:
    try:
        resp = curl_client.get(f"{MAIN_HOST}/api/tools", CHILD_USER, CHILD_PASSWORD, insecure=INSECURE_TLS)
    except curl_client.CurlError as exc:
        return [], f"Could not reach main host: {exc}"
    if resp.status == 401:
        return [], "Main host rejected these credentials (401)."
    if resp.status != 200:
        return [], f"Main host returned HTTP {resp.status}."
    try:
        return json.loads(resp.text), None
    except json.JSONDecodeError:
        return [], "Main host returned a response that wasn't valid JSON."


def _remote_list_groups() -> tuple[list[dict], str | None]:
    try:
        resp = curl_client.get(f"{MAIN_HOST}/api/groups", CHILD_USER, CHILD_PASSWORD, insecure=INSECURE_TLS)
    except curl_client.CurlError as exc:
        return [], f"Could not reach main host: {exc}"
    if resp.status == 401:
        return [], "Main host rejected these credentials (401)."
    if resp.status != 200:
        return [], f"Main host returned HTTP {resp.status}."
    try:
        return json.loads(resp.text), None
    except json.JSONDecodeError:
        return [], "Main host returned a response that wasn't valid JSON."


def _maybe_git_push(config: AppConfig, shared_dir: Path, commit_message: str) -> str | None:
    """Best-effort git push of `shared_dir` after a skill install/delete/edit.

    Returns an error detail string if a push was attempted and failed,
    `None` otherwise — including when it's not configured at all (see
    AppConfig.git_push_username/git_push_password). Never raises: a
    failed push must not block the action that triggered it.
    """
    if not (config.git_push_username and config.git_push_password):
        return None
    return git_push.push_shared_dir(
        shared_dir,
        username=config.git_push_username,
        password=config.git_push_password,
        commit_message=commit_message,
    )


def _tools_for_display(shared_dir: Path) -> list[dict]:
    """Skills as the Skills page and the JSON API need them.

    Built-ins come first, then everything else alphabetically. They're
    app-provided helpers that are always present and never change, so
    burying them among user skills means scrolling past them every time;
    pinned to the top they read as a separate, stable section.

    Shared by the page and `/api/tools` so a child relay renders the same
    order and shows the same `builtin` badge — the badge silently went
    missing on a relay before, because the API didn't include the flag.
    """
    entries = [
        {**registry.manifest_to_dict(m), "builtin": builtin_skills.is_builtin(m.name)}
        for m in registry.list_tools(shared_dir)
    ]
    # `not x["builtin"]` sorts built-ins first; the name tiebreak keeps the
    # order stable and predictable within each group.
    return sorted(entries, key=lambda t: (not t["builtin"], t["name"].lower()))


def _render(request: Request, **extra):
    """Renders the Skills page (templates/index.html)."""
    error = extra.get("error")
    message = extra.get("message")

    if MAIN_HOST:
        tools, remote_error = _remote_list_tools()
        error = error or remote_error
        address_label = MAIN_HOST
        bitbucket_base_url = ""
        git_push_username = ""
        git_push_password_set = False
        instructions = ""
        group_names = [g["name"] for g in _remote_list_groups()[0]]
    else:
        config = load_config()
        shared_dir = config.shared_dir_path
        shared_dir.mkdir(parents=True, exist_ok=True)
        # Built-in skills reappear here rather than only in the MCP server,
        # so the admin sees the same list an agent does.
        builtin_skills.ensure_builtin_skills(shared_dir)
        tools = _tools_for_display(shared_dir)
        address_label = str(shared_dir)
        bitbucket_base_url = config.bitbucket_base_url or ""
        git_push_username = config.git_push_username or ""
        # Never echo the stored password back into the page — just
        # whether one is already set, so the form can show a "уже
        # установлен" placeholder instead of leaking it into the HTML.
        git_push_password_set = bool(config.git_push_password)
        instructions = config.effective_instructions
        group_names = [g.name for g in group_store.list_groups(shared_dir)]

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "remote": bool(MAIN_HOST),
            "read_only": READ_ONLY,
            "active_page": "skills",
            "shared_dir": address_label,
            "bitbucket_base_url": bitbucket_base_url,
            "git_push_username": git_push_username,
            "git_push_password_set": git_push_password_set,
            "instructions": instructions,
            "group_names": group_names,
            "tools": tools,
            "message": message,
            "error": error,
            # index.html's trailing-slash self-correct script must only
            # run when the browser's address bar is actually the app
            # root: this same template/_render() is also used to answer
            # POST /tools/upload, /tools/{name}/delete, /config/shared-dir
            # directly (no redirect, to stay under the proxy prefix — see
            # PLAN.md), which leaves the address bar on *that* POST's own
            # URL, which never ends in "/". Without this guard the script
            # "corrected" that by re-navigating (GET) to the same path
            # plus "/", which 405s since those are POST-only routes.
            "self_correct": request.url.path in ("/", ""),
        },
    )


def _render_users(request: Request, **extra):
    """Renders the Users page (templates/users.html). Main-host only —
    a child relay has no local shared directory or users of its own."""
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "remote": bool(MAIN_HOST),
            "active_page": "users",
            "end_users": users.list_users(),
            "message": extra.get("message"),
            "error": extra.get("error"),
        },
    )


def _render_docs(request: Request):
    """Renders the Docs page (templates/docs.html). Main-host only, same
    reasoning as Users — a child relay has no local shared directory to
    document a path for."""
    config = load_config()
    return templates.TemplateResponse(
        request,
        "docs.html",
        {
            "remote": bool(MAIN_HOST),
            "active_page": "docs",
            "shared_dir": str(config.shared_dir_path),
        },
    )


def _render_files(request: Request, **extra):
    """Renders the Files page (templates/files.html).

    Main host only: a child relay has no local shared directory of its own
    to list files from, same reasoning as Users/Docs.
    """
    if MAIN_HOST:
        files, remote_error = _remote_list_files()
        return templates.TemplateResponse(
            request,
            "files.html",
            {
                "remote": True,
                "read_only": READ_ONLY,
                "active_page": "files",
                "shared_dir": MAIN_HOST,
                "tree": files,
                "message": extra.get("message"),
                "error": extra.get("error") or remote_error,
            },
        )

    config = load_config()
    shared_dir = config.shared_dir_path
    shared_dir.mkdir(parents=True, exist_ok=True)
    return templates.TemplateResponse(
        request,
        "files.html",
        {
            "remote": False,
            "read_only": READ_ONLY,
            "active_page": "files",
            "shared_dir": str(shared_dir),
            "tree": repo_files.list_files(shared_dir),
            "message": extra.get("message"),
            "error": extra.get("error"),
        },
    )


def _redirect_to_files(request: Request, *, message: str | None = None, error: str | None = None):
    target = _relative_redirect(request, "files")
    params = {k: v for k, v in (("message", message), ("error", error)) if v}
    if params:
        separator = "&" if "?" in target else "?"
        target = f"{target}{separator}{urlencode(params)}"
    return RedirectResponse(target, status_code=303)


def _render_groups(request: Request, **extra):
    """Renders the Groups page (templates/groups.html). Works on both the
    main host and a child relay — unlike Users/Docs, groups are shared
    state an admin on either side plausibly wants to manage, the same
    way skills themselves already work through a relay."""
    if MAIN_HOST:
        groups, remote_error = _remote_list_groups()
        error = extra.get("error") or remote_error
    else:
        config = load_config()
        groups = [{"name": g.name, "description": g.description} for g in group_store.list_groups(config.shared_dir_path)]
        error = extra.get("error")

    return templates.TemplateResponse(
        request,
        "groups.html",
        {
            "remote": bool(MAIN_HOST),
            "read_only": READ_ONLY,
            "active_page": "groups",
            "groups": groups,
            "message": extra.get("message"),
            "error": error,
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: str | None = None):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if not auth.verify_admin(username, password):
        return RedirectResponse(_relative_redirect(request, "login?error=1"), status_code=303)

    response = RedirectResponse(_relative_redirect(request, "."), status_code=303)
    response.set_cookie(
        session.COOKIE_NAME,
        session.create_token(username),
        max_age=session.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@app.post("/logout")
def logout(request: Request):
    response = RedirectResponse(_relative_redirect(request, "login"), status_code=303)
    response.delete_cookie(session.COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    _admin: str | None = Depends(require_admin_page),
):
    return _render(request, message=message, error=error)


@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request, _admin: str | None = Depends(require_admin_page)):
    if MAIN_HOST:
        raise HTTPException(status_code=404, detail="Not found")
    return _render_users(request)


@app.get("/docs", response_class=HTMLResponse)
def docs_page(request: Request, _admin: str | None = Depends(require_admin_page)):
    if MAIN_HOST:
        raise HTTPException(status_code=404, detail="Not found")
    return _render_docs(request)


@app.get("/groups", response_class=HTMLResponse)
def groups_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    _admin: str | None = Depends(require_admin_page),
):
    return _render_groups(request, message=message, error=error)


@app.post("/groups")
def create_group_route(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    overwrite: bool = Form(False),
    _admin: str | None = Depends(require_admin_page),
):
    if READ_ONLY:
        return _redirect_to_groups(request, error=READ_ONLY_MESSAGE)

    if MAIN_HOST:
        try:
            resp = curl_client.post(
                f"{MAIN_HOST}/api/groups",
                CHILD_USER,
                CHILD_PASSWORD,
                fields={"name": name, "description": description, "overwrite": "true" if overwrite else "false"},
                insecure=INSECURE_TLS,
            )
        except curl_client.CurlError as exc:
            return _redirect_to_groups(request, error=f"Не удалось подключиться к основному хосту: {exc}")
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError:
            payload = {}
        if resp.status != 200:
            return _redirect_to_groups(request, error=payload.get("error", f"Основной хост вернул HTTP {resp.status}."))
        message = f"Группа «{payload['name']}» создана."
        if payload.get("git_push_error"):
            message += f" Внимание: git push на основном хосте не выполнен ({payload['git_push_error']})."
        return _redirect_to_groups(request, message=message)

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        group = group_store.create_group(shared_dir, name, description, overwrite=overwrite)
    except (ToolValidationError, group_store.GroupAlreadyExistsError) as exc:
        return _redirect_to_groups(request, error=str(exc))
    message = f"Группа «{group.name}» создана."
    push_error = _maybe_git_push(config, shared_dir, f"Add group '{group.name}'")
    if push_error:
        message += f" Внимание: git push не выполнен ({push_error})."
    return _redirect_to_groups(request, message=message)


@app.post("/groups/{name}/delete")
def delete_group_route(request: Request, name: str, _admin: str | None = Depends(require_admin_page)):
    if READ_ONLY:
        return _redirect_to_groups(request, error=READ_ONLY_MESSAGE)

    if MAIN_HOST:
        try:
            resp = curl_client.post(
                f"{MAIN_HOST}/api/groups/{name}/delete", CHILD_USER, CHILD_PASSWORD, insecure=INSECURE_TLS
            )
        except curl_client.CurlError as exc:
            return _redirect_to_groups(request, error=f"Не удалось подключиться к основному хосту: {exc}")
        if resp.status == 404:
            return _redirect_to_groups(request, error=f"Группа «{name}» не найдена (возможно, уже удалена).")
        if resp.status != 200:
            return _redirect_to_groups(request, error=f"Основной хост вернул HTTP {resp.status}.")
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError:
            payload = {}
        message = f"Группа «{name}» удалена."
        if payload.get("git_push_error"):
            message += f" Внимание: git push на основном хосте не выполнен ({payload['git_push_error']})."
        return _redirect_to_groups(request, message=message)

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        group_store.delete_group(shared_dir, name)
    except group_store.GroupNotFoundError:
        return _redirect_to_groups(request, error=f"Группа «{name}» не найдена (возможно, уже удалена).")
    message = f"Группа «{name}» удалена."
    push_error = _maybe_git_push(config, shared_dir, f"Remove group '{name}'")
    if push_error:
        message += f" Внимание: git push не выполнен ({push_error})."
    return _redirect_to_groups(request, message=message)


@app.post("/config/shared-dir")
def set_shared_dir(
    request: Request,
    path: str = Form(...),
    bitbucket_base_url: str = Form(""),
    git_push_username: str = Form(""),
    git_push_password: str = Form(""),
    instructions: str = Form(""),
    _admin: str | None = Depends(require_admin_page),
):
    if MAIN_HOST:
        raise HTTPException(status_code=403, detail="The shared directory is managed on the main host only.")
    new_path = Path(path).expanduser()
    new_path.mkdir(parents=True, exist_ok=True)

    # A blank git_push_username/git_push_password means "leave it as is" —
    # the password field in particular is never pre-filled with the real
    # value (see _render()), so submitting the form without retyping it
    # must not silently disable the push. `instructions` is pre-filled
    # with the *effective* value (stored or default) rather than being
    # kept-as-is-if-blank — so clearing the textarea deliberately resets
    # back to DEFAULT_INSTRUCTIONS rather than becoming unrecoverable.
    existing = load_config()
    save_config(
        AppConfig(
            shared_dir=str(new_path),
            bitbucket_base_url=bitbucket_base_url or None,
            git_push_username=git_push_username or existing.git_push_username,
            git_push_password=git_push_password or existing.git_push_password,
            instructions=instructions or None,
        )
    )
    return _redirect_to_root(request, message=f"Общая директория с навыками установлена на {new_path}")


@app.post("/config/instructions/reset")
def reset_instructions(request: Request, _admin: str | None = Depends(require_admin_page)):
    """One-click revert of the agent's system instructions to
    DEFAULT_INSTRUCTIONS — the same effect as clearing the textarea in
    the "⚙" dialog and saving, as a dedicated action so an admin doesn't
    have to retype or blank the field by hand."""
    if MAIN_HOST:
        raise HTTPException(status_code=403, detail="Instructions are managed on the main host only.")
    config = load_config()
    config.instructions = None
    save_config(config)
    return _redirect_to_root(request, message="Системная инструкция сброшена к стандартной.")


@app.get("/files", response_class=HTMLResponse)
def files_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    _admin: str | None = Depends(require_admin_page),
):
    return _render_files(request, message=message, error=error)


@app.post("/files/upload")
async def upload_files(
    request: Request,
    archive: UploadFile = File(...),
    overwrite: str | None = Form(None),
    _admin: str | None = Depends(require_admin_page),
):
    """Unpacks an uploaded zip into the repo root as ordinary files."""
    if MAIN_HOST:
        result = _forward_upload("/api/files", archive, {"overwrite": overwrite or ""})
        body = json.loads(result.body)
        if result.status_code != 200:
            return _redirect_to_files(request, error=body.get("error", "основной хост отклонил загрузку"))
        written = body.get("written") or []
        message = f"Загружено файлов: {len(written)}."
        if body.get("git_push_error"):
            return _redirect_to_files(request, error=f"{message} Внимание: git push не выполнен ({body['git_push_error']}).")
        return _redirect_to_files(request, message=message)
    if READ_ONLY:
        return _redirect_to_files(request, error=READ_ONLY_MESSAGE)

    shared_dir = load_config().shared_dir_path
    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / "upload.zip"
        archive_path.write_bytes(await archive.read())
        try:
            written = repo_files.install_archive(
                shared_dir, archive_path, overwrite=bool(overwrite)
            )
        except repo_files.FileValidationError as exc:
            return _redirect_to_files(request, error=str(exc))

    push_error = _maybe_git_push(load_config(), shared_dir, f"Upload files ({len(written)})")
    message = f"Загружено файлов: {len(written)}."
    if push_error:
        return _redirect_to_files(request, error=f"{message} Внимание: git push не выполнен ({push_error}).")
    return _redirect_to_files(request, message=message)


@app.post("/files/save")
def save_file(
    request: Request,
    path: str = Form(...),
    content: str = Form(""),
    _admin: str | None = Depends(require_admin_page),
):
    if MAIN_HOST:
        result = _forward_post("/api/files/save", {"path": path, "content": content})
        body = json.loads(result.body)
        if result.status_code != 200:
            return _redirect_to_files(request, error=body.get("error", "основной хост отклонил сохранение"))
        message = f"Файл «{path}» сохранён."
        if body.get("git_push_error"):
            return _redirect_to_files(request, error=f"{message} Внимание: git push не выполнен ({body['git_push_error']}).")
        return _redirect_to_files(request, message=message)
    if READ_ONLY:
        return _redirect_to_files(request, error=READ_ONLY_MESSAGE)

    shared_dir = load_config().shared_dir_path
    try:
        repo_files.write_file(shared_dir, path, content)
    except repo_files.FileValidationError as exc:
        return _redirect_to_files(request, error=str(exc))

    push_error = _maybe_git_push(load_config(), shared_dir, f"Edit file '{path}'")
    message = f"Файл «{path}» сохранён."
    if push_error:
        return _redirect_to_files(request, error=f"{message} Внимание: git push не выполнен ({push_error}).")
    return _redirect_to_files(request, message=message)


@app.post("/files/rename")
def rename_file(
    request: Request,
    path: str = Form(...),
    new_name: str = Form(...),
    _admin: str | None = Depends(require_admin_page),
):
    if READ_ONLY:
        return _redirect_to_files(request, error=READ_ONLY_MESSAGE)

    if MAIN_HOST:
        result = _forward_post("/api/files/rename", {"path": path, "new_name": new_name})
        body = json.loads(result.body)
        if result.status_code != 200:
            return _redirect_to_files(request, error=body.get("error", "основной хост отклонил переименование"))
        message = f"«{path}» переименован в «{body.get('path', new_name)}»."
        if body.get("git_push_error"):
            return _redirect_to_files(request, error=f"{message} Внимание: git push не выполнен ({body['git_push_error']}).")
        return _redirect_to_files(request, message=message)

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        new_path = repo_files.rename_path(shared_dir, path, new_name)
    except repo_files.FileValidationError as exc:
        return _redirect_to_files(request, error=str(exc))

    push_error = _maybe_git_push(config, shared_dir, f"Rename file '{path}' -> '{new_path}'")
    message = f"«{path}» переименован в «{new_path}»."
    if push_error:
        return _redirect_to_files(request, error=f"{message} Внимание: git push не выполнен ({push_error}).")
    return _redirect_to_files(request, message=message)


@app.post("/files/delete")
def delete_file(
    request: Request,
    path: str = Form(...),
    _admin: str | None = Depends(require_admin_page),
):
    if MAIN_HOST:
        result = _forward_post("/api/files/delete", {"path": path})
        body = json.loads(result.body)
        if result.status_code != 200:
            return _redirect_to_files(request, error=body.get("error", "основной хост отклонил удаление"))
        message = f"Файл «{path}» удалён."
        if body.get("git_push_error"):
            return _redirect_to_files(request, error=f"{message} Внимание: git push не выполнен ({body['git_push_error']}).")
        return _redirect_to_files(request, message=message)
    if READ_ONLY:
        return _redirect_to_files(request, error=READ_ONLY_MESSAGE)

    shared_dir = load_config().shared_dir_path
    try:
        repo_files.delete_path(shared_dir, path)
    except repo_files.FileValidationError as exc:
        return _redirect_to_files(request, error=str(exc))

    push_error = _maybe_git_push(load_config(), shared_dir, f"Delete file '{path}'")
    message = f"Файл «{path}» удалён."
    if push_error:
        return _redirect_to_files(request, error=f"{message} Внимание: git push не выполнен ({push_error}).")
    return _redirect_to_files(request, message=message)


@app.get("/files/raw")
def raw_file(
    path: str,
    _admin: str | None = Depends(require_admin_page),
):
    """Plain-text contents for the editor dialog (no download headers)."""
    if MAIN_HOST:
        resp = curl_client.get(
            f"{MAIN_HOST}/api/files/raw?{urlencode({'path': path})}",
            CHILD_USER, CHILD_PASSWORD, insecure=INSECURE_TLS,
        )
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail=resp.text)
        return PlainTextResponse(resp.text)
    shared_dir = load_config().shared_dir_path
    try:
        return PlainTextResponse(repo_files.read_file(shared_dir, path))
    except repo_files.FileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/files/download")
def download_file(
    path: str,
    _admin: str | None = Depends(require_admin_page),
):
    """Serves a single file back, including binaries — the page lists them
    but can't edit them, so there has to be some way to get them out."""
    if MAIN_HOST:
        return _forward_download("/api/files/download", {"path": path}, filename=Path(path).name)
    shared_dir = load_config().shared_dir_path
    try:
        target = repo_files.read_file_path(shared_dir, path)
    except repo_files.FileValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(target, filename=target.name)


@app.post("/api/users/register")
def register_user(username: str = Form(...)):
    """Public — called by the distributed connection notebook's
    self-registration curl request (see notebooks/connect_to_mcp.ipynb).
    Tracking only, not an access-control boundary (see common/users.py)."""
    if MAIN_HOST:
        raise HTTPException(status_code=404, detail="Not found")
    users.record_user(username)
    return {"ok": True}


@app.post("/tools/upload")
async def upload_tool(
    request: Request,
    archive: UploadFile = File(...),
    overwrite: bool = Form(False),
    name: str = Form(""),
    description: str = Form(""),
    group: str = Form(""),
    _admin: str | None = Depends(require_admin_page),
):
    if READ_ONLY:
        return _redirect_to_root(request, error=READ_ONLY_MESSAGE)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(await archive.read())
        tmp_path = Path(tmp.name)

    try:
        if MAIN_HOST:
            try:
                resp = curl_client.post(
                    f"{MAIN_HOST}/api/tools",
                    CHILD_USER,
                    CHILD_PASSWORD,
                    fields={
                        "overwrite": "true" if overwrite else "false",
                        "name": name,
                        "description": description,
                        "group": group,
                    },
                    files={"archive": str(tmp_path)},
                    insecure=INSECURE_TLS,
                )
            except curl_client.CurlError as exc:
                return _redirect_to_root(request, error=f"Не удалось подключиться к основному хосту: {exc}")
            try:
                payload = json.loads(resp.text)
            except json.JSONDecodeError:
                return _redirect_to_root(
                    request, error=f"Основной хост вернул HTTP {resp.status} с нечитаемым телом ответа."
                )
            if resp.status != 200:
                return _redirect_to_root(
                    request, error=payload.get("error", f"Основной хост вернул HTTP {resp.status}.")
                )
            message = f"«{payload['name']}» установлен для всех пользователей."
            if payload.get("git_push_error"):
                message += f" Внимание: git push на основном хосте не выполнен ({payload['git_push_error']})."
            return _redirect_to_root(request, message=message)

        config = load_config()
        shared_dir = config.shared_dir_path
        try:
            manifest = registry.install_tool(
                tmp_path,
                shared_dir,
                overwrite=overwrite,
                name=name or None,
                description=description or None,
                group=group or None,
            )
        except (ToolValidationError, ToolAlreadyExistsError) as exc:
            return _redirect_to_root(request, error=str(exc))
        message = f"«{manifest.name}» установлен для всех пользователей."
        push_error = _maybe_git_push(config, shared_dir, f"Add skill '{manifest.name}'")
        if push_error:
            message += f" Внимание: git push не выполнен ({push_error})."
        return _redirect_to_root(request, message=message)
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/tools/create-doc-skill")
def create_doc_skill_route(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    body: str = Form(...),
    group: str = Form(""),
    overwrite: bool = Form(False),
    _admin: str | None = Depends(require_admin_page),
):
    """Creates a doc skill (SKILL.md bundle) straight from text typed into
    the admin UI — no archive upload at all. Per explicit request: an
    admin should be able to write the skill's Markdown directly instead
    of always having to build and upload a `.zip`."""
    if READ_ONLY:
        return _redirect_to_root(request, error=READ_ONLY_MESSAGE)

    if MAIN_HOST:
        try:
            resp = curl_client.post(
                f"{MAIN_HOST}/api/tools/create-doc-skill",
                CHILD_USER,
                CHILD_PASSWORD,
                fields={
                    "name": name,
                    "description": description,
                    "body": body,
                    "group": group,
                    "overwrite": "true" if overwrite else "false",
                },
                insecure=INSECURE_TLS,
            )
        except curl_client.CurlError as exc:
            return _redirect_to_root(request, error=f"Не удалось подключиться к основному хосту: {exc}")
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError:
            payload = {}
        if resp.status != 200:
            return _redirect_to_root(request, error=payload.get("error", f"Основной хост вернул HTTP {resp.status}."))
        message = f"«{payload['name']}» создан и установлен для всех пользователей."
        if payload.get("git_push_error"):
            message += f" Внимание: git push на основном хосте не выполнен ({payload['git_push_error']})."
        return _redirect_to_root(request, message=message)

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        manifest = registry.create_doc_skill(
            shared_dir, name=name, description=description, body=body, group=group, overwrite=overwrite
        )
    except (ToolValidationError, ToolAlreadyExistsError) as exc:
        return _redirect_to_root(request, error=str(exc))
    message = f"«{manifest.name}» создан и установлен для всех пользователей."
    push_error = _maybe_git_push(config, shared_dir, f"Add skill '{manifest.name}'")
    if push_error:
        message += f" Внимание: git push не выполнен ({push_error})."
    return _redirect_to_root(request, message=message)


@app.post("/tools/{name}/rename")
def rename_tool_route(
    request: Request,
    name: str,
    new_name: str = Form(...),
    _admin: str | None = Depends(require_admin_page),
):
    """Renames a skill: the folder, the MCP tool name, and — for a code
    skill — the entrypoint function itself, which is where its name lives."""
    if READ_ONLY:
        return _redirect_to_root(request, error=READ_ONLY_MESSAGE)
    if (refusal := _reject_builtin(name, request)) is not None:
        return refusal

    new_name = new_name.strip()
    if new_name == name:
        return _redirect_to_root(request, message="Имя не изменилось.")

    if MAIN_HOST:
        resp = _forward_post(
            f"/api/tools/{name}/rename", {"new_name": new_name}
        )
        payload = json.loads(resp.body) if resp.body else {}
        if resp.status_code != 200:
            return _redirect_to_root(request, error=payload.get("error", f"основной хост вернул HTTP {resp.status_code}."))
        message = f"«{name}» переименован в «{payload.get('name', new_name)}»."
        if payload.get("git_push_error"):
            return _redirect_to_root(request, error=f"{message} Внимание: git push не выполнен ({payload['git_push_error']}).")
        return _redirect_to_root(request, message=message)

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        manifest = registry.rename_tool(shared_dir, name, new_name)
    except ToolNotFoundError:
        return _redirect_to_root(request, error=f"«{name}» не установлен.")
    except ToolAlreadyExistsError:
        return _redirect_to_root(request, error=f"Навык «{new_name}» уже существует.")
    except ToolValidationError as exc:
        return _redirect_to_root(request, error=str(exc))

    push_error = _maybe_git_push(config, shared_dir, f"Rename skill '{name}' -> '{manifest.name}'")
    message = f"«{name}» переименован в «{manifest.name}»."
    if push_error:
        return _redirect_to_root(request, error=f"{message} Внимание: git push не выполнен ({push_error}).")
    return _redirect_to_root(request, message=message)


@app.post("/tools/{name}/delete")
def delete_tool(request: Request, name: str, _admin: str | None = Depends(require_admin_page)):
    if READ_ONLY:
        return _redirect_to_root(request, error=READ_ONLY_MESSAGE)
    if (refusal := _reject_builtin(name, request)) is not None:
        return refusal
    if MAIN_HOST:
        try:
            resp = curl_client.post(
                f"{MAIN_HOST}/api/tools/{name}/delete",
                CHILD_USER,
                CHILD_PASSWORD,
                insecure=INSECURE_TLS,
            )
        except curl_client.CurlError as exc:
            return _redirect_to_root(request, error=f"Не удалось подключиться к основному хосту: {exc}")
        if resp.status == 404:
            return _redirect_to_root(request, error=f"«{name}» не установлен на основном хосте (возможно, уже удалён).")
        if resp.status != 200:
            return _redirect_to_root(request, error=f"Основной хост вернул HTTP {resp.status}.")
        message = f"«{name}» удалён для всех пользователей."
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError:
            payload = {}
        if payload.get("git_push_error"):
            message += f" Внимание: git push на основном хосте не выполнен ({payload['git_push_error']})."
        return _redirect_to_root(request, message=message)

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        registry.remove_tool(shared_dir, name)
    except ToolNotFoundError:
        return _redirect_to_root(request, error=f"«{name}» не установлен (возможно, уже удалён) — обновите страницу.")
    message = f"«{name}» удалён для всех пользователей."
    push_error = _maybe_git_push(config, shared_dir, f"Remove skill '{name}'")
    if push_error:
        message += f" Внимание: git push не выполнен ({push_error})."
    return _redirect_to_root(request, message=message)


@app.get("/tools/{name}/download")
def download_tool(name: str, _admin: str | None = Depends(require_admin_page)):
    """Downloads an installed skill back as a `.zip` — the reverse of
    `/tools/upload`. Not a mutation, so this stays available even under
    MCP_TOOLS_READ_ONLY=1."""
    if MAIN_HOST:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            status = curl_client.download(
                f"{MAIN_HOST}/api/tools/{name}/download", CHILD_USER, CHILD_PASSWORD, tmp_path, insecure=INSECURE_TLS
            )
        except curl_client.CurlError as exc:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=502, detail=f"Не удалось подключиться к основному хосту: {exc}") from exc
        if status != 200:
            try:
                payload = json.loads(tmp_path.read_text())
            except (OSError, json.JSONDecodeError):
                payload = {}
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=status, detail=payload.get("error", f"Основной хост вернул HTTP {status}."))
        return FileResponse(
            tmp_path,
            filename=f"{name}.zip",
            media_type="application/zip",
            background=BackgroundTask(tmp_path.unlink, missing_ok=True),
        )

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        archive_path = registry.build_download_archive(shared_dir, name)
    except ToolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        archive_path,
        filename=f"{name}.zip",
        media_type="application/zip",
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


@app.post("/tools/{name}/edit-description")
def edit_doc_skill_description(
    request: Request,
    name: str,
    description: str = Form(...),
    group: str = Form(""),
    _admin: str | None = Depends(require_admin_page),
):
    """Edits a doc skill's (SKILL.md bundle's) description/group in place
    — the only skill metadata that's ever edited without re-uploading the
    whole archive. Code skills have no equivalent: their description is
    the function's own docstring and its group is `__group__`, both
    edited by editing the .py file directly."""
    if READ_ONLY:
        return _redirect_to_root(request, error=READ_ONLY_MESSAGE)
    if (refusal := _reject_builtin(name, request)) is not None:
        return refusal
    if MAIN_HOST:
        try:
            resp = curl_client.post(
                f"{MAIN_HOST}/api/tools/{name}/edit-description",
                CHILD_USER,
                CHILD_PASSWORD,
                fields={"description": description, "group": group},
                insecure=INSECURE_TLS,
            )
        except curl_client.CurlError as exc:
            return _redirect_to_root(request, error=f"Не удалось подключиться к основному хосту: {exc}")
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError:
            payload = {}
        if resp.status != 200:
            return _redirect_to_root(
                request, error=payload.get("error", f"Основной хост вернул HTTP {resp.status}.")
            )
        message = f"«{name}» обновлён."
        if payload.get("git_push_error"):
            message += f" Внимание: git push на основном хосте не выполнен ({payload['git_push_error']})."
        return _redirect_to_root(request, message=message)

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        registry.update_doc_skill_metadata(shared_dir, name, description=description, group=group)
    except (ToolValidationError, ToolNotFoundError) as exc:
        return _redirect_to_root(request, error=str(exc))
    message = f"«{name}» обновлён."
    push_error = _maybe_git_push(config, shared_dir, f"Edit skill '{name}' description")
    if push_error:
        message += f" Внимание: git push не выполнен ({push_error})."
    return _redirect_to_root(request, message=message)


# --- JSON API -----------------------------------------------------------
# Always operates on this host's own local registry, regardless of mode:
# this is the surface a child relay's curl calls hit on the *main* host.
# It is not meaningful to call these on a child relay itself (it has no
# local shared directory), but they're left enabled rather than special
# cased on MAIN_HOST. Gated with HTTP Basic Auth (require_admin_api), not
# the cookie-session login the HTML routes use — see module docstring.


@app.get("/api/tools")
def api_list_tools(_admin: str | None = Depends(require_admin_api)):
    if MAIN_HOST:
        tools, error = _remote_list_tools()
        if error:
            raise HTTPException(status_code=502, detail=error)
        return tools
    config = load_config()
    return _tools_for_display(config.shared_dir_path)


@app.post("/api/tools")
async def api_upload_tool(
    archive: UploadFile = File(...),
    overwrite: bool = Form(False),
    name: str = Form(""),
    description: str = Form(""),
    group: str = Form(""),
    _admin: str | None = Depends(require_admin_api),
):
    if READ_ONLY:
        return JSONResponse(status_code=403, content={"error": READ_ONLY_MESSAGE})

    config = load_config()
    shared_dir = config.shared_dir_path

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(await archive.read())
        tmp_path = Path(tmp.name)

    try:
        manifest = registry.install_tool(
            tmp_path,
            shared_dir,
            overwrite=overwrite,
            name=name or None,
            description=description or None,
            group=group or None,
        )
    except ToolValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except ToolAlreadyExistsError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})
    finally:
        tmp_path.unlink(missing_ok=True)

    push_error = _maybe_git_push(config, shared_dir, f"Add skill '{manifest.name}'")
    return {"name": manifest.name, "git_push_error": push_error}


@app.post("/api/tools/create-doc-skill")
def api_create_doc_skill(
    name: str = Form(...),
    description: str = Form(...),
    body: str = Form(...),
    group: str = Form(""),
    overwrite: bool = Form(False),
    _admin: str | None = Depends(require_admin_api),
):
    if READ_ONLY:
        return JSONResponse(status_code=403, content={"error": READ_ONLY_MESSAGE})

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        manifest = registry.create_doc_skill(
            shared_dir, name=name, description=description, body=body, group=group, overwrite=overwrite
        )
    except ToolValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except ToolAlreadyExistsError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})

    push_error = _maybe_git_push(config, shared_dir, f"Add skill '{manifest.name}'")
    return {"name": manifest.name, "git_push_error": push_error}


@app.get("/api/tools/{name}/download")
def api_download_tool(name: str, _admin: str | None = Depends(require_admin_api)):
    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        archive_path = registry.build_download_archive(shared_dir, name)
    except ToolNotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    return FileResponse(
        archive_path,
        filename=f"{name}.zip",
        media_type="application/zip",
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


@app.post("/api/tools/{name}/rename")
def api_rename_tool(
    name: str,
    new_name: str = Form(...),
    _admin: str | None = Depends(require_admin_api),
):
    if READ_ONLY:
        return JSONResponse(status_code=403, content={"error": READ_ONLY_MESSAGE})
    if builtin_skills.is_builtin(name):
        return JSONResponse(status_code=403, content={"error": BUILTIN_SKILL_MESSAGE})
    if MAIN_HOST:
        return _forward_post(f"/api/tools/{name}/rename", {"new_name": new_name})

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        manifest = registry.rename_tool(shared_dir, name, new_name.strip())
    except ToolNotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except (ToolAlreadyExistsError, ToolValidationError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    push_error = _maybe_git_push(config, shared_dir, f"Rename skill '{name}' -> '{manifest.name}'")
    return {"name": manifest.name, "git_push_error": push_error}


@app.post("/api/tools/{name}/delete")
def api_delete_tool(name: str, _admin: str | None = Depends(require_admin_api)):
    if READ_ONLY:
        return JSONResponse(status_code=403, content={"error": READ_ONLY_MESSAGE})
    if builtin_skills.is_builtin(name):
        # Also enforced here: a child relay reaches this endpoint directly,
        # so guarding only the HTML route would leave a hole.
        return JSONResponse(status_code=403, content={"error": BUILTIN_SKILL_MESSAGE})

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        registry.remove_tool(shared_dir, name)
    except ToolNotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    push_error = _maybe_git_push(config, shared_dir, f"Remove skill '{name}'")
    return {"name": name, "git_push_error": push_error}


@app.post("/api/tools/{name}/edit-description")
def api_edit_doc_skill_description(
    name: str,
    description: str = Form(...),
    group: str = Form(""),
    _admin: str | None = Depends(require_admin_api),
):
    if READ_ONLY:
        return JSONResponse(status_code=403, content={"error": READ_ONLY_MESSAGE})
    if builtin_skills.is_builtin(name):
        return JSONResponse(status_code=403, content={"error": BUILTIN_SKILL_MESSAGE})

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        registry.update_doc_skill_metadata(shared_dir, name, description=description, group=group)
    except ToolNotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except ToolValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    push_error = _maybe_git_push(config, shared_dir, f"Edit skill '{name}' description")
    return {"name": name, "git_push_error": push_error}


@app.get("/api/files")
def api_list_files(_admin: str | None = Depends(require_admin_api)):
    """Repo files, for a child relay to mirror the Files tab.

    Exposes only what the page needs (path/name/depth/size/flags) — the
    built-in dataclass is richer than a JSON contract should be, and keeping
    the shape explicit here means the relay can't silently depend on an
    internal field.
    """
    if MAIN_HOST:
        files, error = _remote_list_files()
        if error:
            raise HTTPException(status_code=502, detail=error)
        return files
    shared_dir = load_config().shared_dir_path
    return [
        {
            "path": f.path,
            "name": f.name,
            "depth": f.depth,
            "size": f.size,
            "is_dir": f.is_dir,
            "editable": f.editable,
            "child_count": f.child_count,
        }
        for f in repo_files.list_files(shared_dir)
    ]


@app.post("/api/files")
async def api_upload_files(
    archive: UploadFile = File(...),
    overwrite: str | None = Form(None),
    _admin: str | None = Depends(require_admin_api),
):
    if MAIN_HOST:
        return _forward_upload("/api/files", archive, {"overwrite": overwrite or ""})
    if READ_ONLY:
        return JSONResponse(status_code=403, content={"error": READ_ONLY_MESSAGE})

    shared_dir = load_config().shared_dir_path
    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / "upload.zip"
        archive_path.write_bytes(await archive.read())
        try:
            written = repo_files.install_archive(shared_dir, archive_path, overwrite=bool(overwrite))
        except repo_files.FileValidationError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})

    push_error = _maybe_git_push(load_config(), shared_dir, f"Upload files ({len(written)})")
    return {"written": written, "git_push_error": push_error}


@app.post("/api/files/save")
def api_save_file(
    path: str = Form(...),
    content: str = Form(""),
    _admin: str | None = Depends(require_admin_api),
):
    if MAIN_HOST:
        resp = _forward_post("/api/files/save", {"path": path, "content": content})
        return resp
    if READ_ONLY:
        return JSONResponse(status_code=403, content={"error": READ_ONLY_MESSAGE})

    shared_dir = load_config().shared_dir_path
    try:
        repo_files.write_file(shared_dir, path, content)
    except repo_files.FileValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    push_error = _maybe_git_push(load_config(), shared_dir, f"Edit file '{path}'")
    return {"path": path, "git_push_error": push_error}


@app.post("/api/files/rename")
def api_rename_file(
    path: str = Form(...),
    new_name: str = Form(...),
    _admin: str | None = Depends(require_admin_api),
):
    if MAIN_HOST:
        return _forward_post("/api/files/rename", {"path": path, "new_name": new_name})
    if READ_ONLY:
        return JSONResponse(status_code=403, content={"error": READ_ONLY_MESSAGE})

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        new_path = repo_files.rename_path(shared_dir, path, new_name)
    except repo_files.FileValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    push_error = _maybe_git_push(config, shared_dir, f"Rename file '{path}' -> '{new_path}'")
    return {"path": new_path, "git_push_error": push_error}


@app.post("/api/files/delete")
def api_delete_file(
    path: str = Form(...),
    _admin: str | None = Depends(require_admin_api),
):
    if MAIN_HOST:
        return _forward_post("/api/files/delete", {"path": path})
    if READ_ONLY:
        return JSONResponse(status_code=403, content={"error": READ_ONLY_MESSAGE})

    shared_dir = load_config().shared_dir_path
    try:
        repo_files.delete_path(shared_dir, path)
    except repo_files.FileValidationError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})

    push_error = _maybe_git_push(load_config(), shared_dir, f"Delete file '{path}'")
    return {"path": path, "git_push_error": push_error}


@app.get("/api/files/raw")
def api_raw_file(
    path: str,
    _admin: str | None = Depends(require_admin_api),
):
    if MAIN_HOST:
        resp = curl_client.get(
            f"{MAIN_HOST}/api/files/raw?{urlencode({'path': path})}",
            CHILD_USER, CHILD_PASSWORD, insecure=INSECURE_TLS,
        )
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail=resp.text)
        return PlainTextResponse(resp.text)
    shared_dir = load_config().shared_dir_path
    try:
        return PlainTextResponse(repo_files.read_file(shared_dir, path))
    except repo_files.FileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/files/download")
def api_download_file(
    path: str,
    _admin: str | None = Depends(require_admin_api),
):
    if MAIN_HOST:
        return _forward_download("/api/files/download", {"path": path}, filename=Path(path).name)
    shared_dir = load_config().shared_dir_path
    try:
        target = repo_files.read_file_path(shared_dir, path)
    except repo_files.FileValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(target, filename=target.name)


@app.get("/api/groups")
def api_list_groups(_admin: str | None = Depends(require_admin_api)):
    if MAIN_HOST:
        groups, error = _remote_list_groups()
        if error:
            raise HTTPException(status_code=502, detail=error)
        return groups
    config = load_config()
    return [{"name": g.name, "description": g.description} for g in group_store.list_groups(config.shared_dir_path)]


@app.post("/api/groups")
def api_create_group(
    name: str = Form(...),
    description: str = Form(...),
    overwrite: bool = Form(False),
    _admin: str | None = Depends(require_admin_api),
):
    if READ_ONLY:
        return JSONResponse(status_code=403, content={"error": READ_ONLY_MESSAGE})

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        group = group_store.create_group(shared_dir, name, description, overwrite=overwrite)
    except group_store.GroupAlreadyExistsError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})
    except ToolValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    push_error = _maybe_git_push(config, shared_dir, f"Add group '{group.name}'")
    return {"name": group.name, "description": group.description, "git_push_error": push_error}


@app.post("/api/groups/{name}/delete")
def api_delete_group(name: str, _admin: str | None = Depends(require_admin_api)):
    if READ_ONLY:
        return JSONResponse(status_code=403, content={"error": READ_ONLY_MESSAGE})

    config = load_config()
    shared_dir = config.shared_dir_path
    try:
        group_store.delete_group(shared_dir, name)
    except group_store.GroupNotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    push_error = _maybe_git_push(config, shared_dir, f"Remove group '{name}'")
    return {"name": name, "git_push_error": push_error}

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

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from ..common import auth, curl_client, registry, session, users
from ..common.config import AppConfig, load_config, save_config
from ..common.manifest import ToolValidationError
from ..common.registry import ToolAlreadyExistsError, ToolNotFoundError

TEMPLATES_DIR = Path(__file__).parent / "templates"

MAIN_HOST = os.environ.get("MCP_TOOLS_MAIN_HOST", "").rstrip("/")
CHILD_USER = os.environ.get("MCP_TOOLS_ADMIN_USER", "")
CHILD_PASSWORD = os.environ.get("MCP_TOOLS_ADMIN_PASSWORD", "")
INSECURE_TLS = os.environ.get("MCP_TOOLS_INSECURE_TLS") == "1"

app = FastAPI(title="MJApp (Lab edition)", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_basic_security = HTTPBasic(auto_error=False)


class NotAuthenticated(Exception):
    pass


@app.exception_handler(NotAuthenticated)
def _redirect_to_login(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    # Deliberately NOT prefixed with _request_base(): jupyter-server-proxy
    # already rewrites Location headers on redirect responses to prepend
    # its own mount prefix (see _rewrite_location_header in its source).
    # Adding the prefix here too would double it up
    # (".../proxy/8766/proxy/8766/login") — see PLAN.md.
    return RedirectResponse("/login", status_code=303)


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


def _request_base(request: Request) -> str:
    """The external mount prefix this app is being proxied under, if any.

    jupyter-server-proxy sets X-Forwarded-Prefix to the path it's proxying
    us from (e.g. "/user/<name>/proxy/8766"). Every link/form action in
    the template is built as f"{base}/<route>" so navigation is correct
    regardless of trailing slash or how deep the current request path is.
    """
    return request.headers.get("x-forwarded-prefix", "").rstrip("/")


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


def _render(request: Request, **extra):
    """Renders the Skills page (templates/index.html)."""
    error = extra.get("error")
    message = extra.get("message")

    if MAIN_HOST:
        tools, remote_error = _remote_list_tools()
        error = error or remote_error
        address_label = MAIN_HOST
        bitbucket_script_path = ""
    else:
        config = load_config()
        shared_dir = config.shared_dir_path
        shared_dir.mkdir(parents=True, exist_ok=True)
        tools = [registry.manifest_to_dict(m) for m in registry.list_tools(shared_dir)]
        address_label = str(shared_dir)
        bitbucket_script_path = config.bitbucket_script_path or ""

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "base": _request_base(request),
            "remote": bool(MAIN_HOST),
            "active_page": "skills",
            "shared_dir": address_label,
            "bitbucket_script_path": bitbucket_script_path,
            "tools": tools,
            "message": message,
            "error": error,
        },
    )


def _render_users(request: Request, **extra):
    """Renders the Users page (templates/users.html). Main-host only —
    a child relay has no local shared directory or users of its own."""
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "base": _request_base(request),
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
            "base": _request_base(request),
            "remote": bool(MAIN_HOST),
            "active_page": "docs",
            "shared_dir": str(config.shared_dir_path),
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: str | None = None):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"base": _request_base(request), "error": error},
    )


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    # See _redirect_to_login: redirect Locations are deliberately
    # prefix-free, since jupyter-server-proxy rewrites them itself.
    if not auth.verify_admin(username, password):
        return RedirectResponse("/login?error=1", status_code=303)

    response = RedirectResponse("/", status_code=303)
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
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(session.COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _admin: str | None = Depends(require_admin_page)):
    return _render(request)


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


@app.post("/config/shared-dir")
def set_shared_dir(
    request: Request,
    path: str = Form(...),
    bitbucket_script_path: str = Form(""),
    _admin: str | None = Depends(require_admin_page),
):
    if MAIN_HOST:
        raise HTTPException(status_code=403, detail="The shared directory is managed on the main host only.")
    new_path = Path(path).expanduser()
    new_path.mkdir(parents=True, exist_ok=True)
    save_config(AppConfig(shared_dir=str(new_path), bitbucket_script_path=bitbucket_script_path or None))
    return _render(request, message=f"Shared tools directory set to {new_path}")


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
    _admin: str | None = Depends(require_admin_page),
):
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
                    fields={"overwrite": "true" if overwrite else "false"},
                    files={"archive": str(tmp_path)},
                    insecure=INSECURE_TLS,
                )
            except curl_client.CurlError as exc:
                return _render(request, error=f"Could not reach main host: {exc}")
            try:
                payload = json.loads(resp.text)
            except json.JSONDecodeError:
                return _render(request, error=f"Main host returned HTTP {resp.status} with an unreadable body.")
            if resp.status != 200:
                return _render(request, error=payload.get("error", f"Main host returned HTTP {resp.status}."))
            return _render(request, message=f"Installed '{payload['name']}' v{payload['version']} for all users.")

        config = load_config()
        shared_dir = config.shared_dir_path
        try:
            manifest = registry.install_tool(tmp_path, shared_dir, overwrite=overwrite)
        except (ToolValidationError, ToolAlreadyExistsError) as exc:
            return _render(request, error=str(exc))
        return _render(request, message=f"Installed '{manifest.name}' v{manifest.version} for all users.")
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/tools/{name}/delete")
def delete_tool(request: Request, name: str, _admin: str | None = Depends(require_admin_page)):
    if MAIN_HOST:
        try:
            resp = curl_client.post(
                f"{MAIN_HOST}/api/tools/{name}/delete",
                CHILD_USER,
                CHILD_PASSWORD,
                insecure=INSECURE_TLS,
            )
        except curl_client.CurlError as exc:
            return _render(request, error=f"Could not reach main host: {exc}")
        if resp.status == 404:
            raise HTTPException(status_code=404, detail=f"tool '{name}' is not installed on the main host")
        if resp.status != 200:
            return _render(request, error=f"Main host returned HTTP {resp.status}.")
        return _render(request, message=f"Removed '{name}' for all users.")

    config = load_config()
    try:
        registry.remove_tool(config.shared_dir_path, name)
    except ToolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _render(request, message=f"Removed '{name}' for all users.")


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
    return [registry.manifest_to_dict(m) for m in registry.list_tools(config.shared_dir_path)]


@app.post("/api/tools")
async def api_upload_tool(
    archive: UploadFile = File(...),
    overwrite: bool = Form(False),
    _admin: str | None = Depends(require_admin_api),
):
    config = load_config()
    shared_dir = config.shared_dir_path

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(await archive.read())
        tmp_path = Path(tmp.name)

    try:
        manifest = registry.install_tool(tmp_path, shared_dir, overwrite=overwrite)
    except ToolValidationError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except ToolAlreadyExistsError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})
    finally:
        tmp_path.unlink(missing_ok=True)

    return {"name": manifest.name, "version": manifest.version}


@app.post("/api/tools/{name}/delete")
def api_delete_tool(name: str, _admin: str | None = Depends(require_admin_api)):
    config = load_config()
    try:
        registry.remove_tool(config.shared_dir_path, name)
    except ToolNotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    return {"name": name}

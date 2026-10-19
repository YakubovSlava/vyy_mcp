from pathlib import Path

from starlette.testclient import TestClient

from jupyter_mcp_tools.common import users
from jupyter_mcp_tools.mcp_server.server import LogMCPUserMiddleware, build_app


def _streamable_app(shared_dir: Path):
    app = build_app(shared_dir)
    starlette_app = app.streamable_http_app()
    starlette_app.add_middleware(LogMCPUserMiddleware)
    return starlette_app


def test_request_with_mcp_user_header_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOOLS_USERS_PATH", str(tmp_path / "users.json"))

    starlette_app = _streamable_app(tmp_path / "shared")
    with TestClient(starlette_app) as client:
        client.get(
            "/mcp",
            headers={"X-MCP-User": "bob.smith", "Accept": "application/json, text/event-stream"},
        )

    recorded = users.list_users()
    assert [u["username"] for u in recorded] == ["bob.smith"]


def test_request_without_mcp_user_header_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_TOOLS_USERS_PATH", str(tmp_path / "users.json"))

    starlette_app = _streamable_app(tmp_path / "shared")
    with TestClient(starlette_app) as client:
        client.get("/mcp", headers={"Accept": "application/json, text/event-stream"})

    assert users.list_users() == []


def test_binding_0_0_0_0_accepts_foreign_host_header(tmp_path):
    # Regression test: FastMCP auto-enables DNS-rebinding protection
    # (locking Host-header validation to 127.0.0.1/localhost patterns)
    # if it sees one of those as `host` *at construction time* — and
    # that decision doesn't change just because something sets
    # app.settings.host afterwards. build_app() must pass host= into
    # FastMCP(...) directly, or every request through a real proxy
    # (whose Host header is some external domain, not 127.0.0.1) gets
    # rejected with 421 regardless of what we bind to. See PLAN.md.
    app = build_app(tmp_path / "shared", host="0.0.0.0", port=8765)
    starlette_app = app.streamable_http_app()
    with TestClient(starlette_app) as client:
        r = client.get(
            "/mcp",
            headers={"Host": "some.external.domain:8888", "Accept": "application/json, text/event-stream"},
        )
    assert r.status_code != 421


def test_binding_127_0_0_1_still_locks_host_header(tmp_path):
    # The flip side: explicitly binding localhost should still get
    # FastMCP's own DNS-rebinding protection (its sensible default for
    # that case) — confirms the fix is about passing `host` through
    # correctly, not about disabling the check altogether.
    app = build_app(tmp_path / "shared", host="127.0.0.1", port=8765)
    starlette_app = app.streamable_http_app()
    with TestClient(starlette_app) as client:
        r = client.get(
            "/mcp",
            headers={"Host": "some.external.domain:8888", "Accept": "application/json, text/event-stream"},
        )
    assert r.status_code == 421

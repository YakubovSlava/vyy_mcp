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

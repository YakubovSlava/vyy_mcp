"""`curl_client` must be byte-exact when forwarding form values.

The relay sends file content through a multipart field, so anything this
module mangles silently corrupts files on the way between a child relay and
the main host.
"""
import pytest

from jupyter_mcp_tools.common import curl_client


class _EchoServer:
    """Minimal HTTP server that echoes back the raw multipart body it got."""

    def __init__(self):
        import http.server
        import socketserver
        import threading

        captured: dict[str, bytes] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                captured["body"] = self.rfile.read(length)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        self._server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.captured = captured
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def echo_server():
    server = _EchoServer()
    try:
        yield server
    finally:
        server.close()


def test_trailing_newline_survives_the_round_trip(echo_server):
    """Regression: `curl -F name=value` strips one trailing newline from the
    value, so every file passed through a child relay lost its final "\\n"."""
    curl_client.post(echo_server.url, "u", "p", fields={"content": "two\n"})
    # The value arrives verbatim, final newline included. (Inline `-F
    # name=value` would have sent "two" -- one byte short, on every hop.)
    assert echo_server.captured["body"].split(b"\r\n\r\n", 1)[1].startswith(b"two\n")


def test_multiple_newlines_are_preserved(echo_server):
    curl_client.post(echo_server.url, "u", "p", fields={"content": "a\n\n\n"})
    body = echo_server.captured["body"]
    assert b"a\n\n\n" in body


def test_values_with_curl_metacharacters_are_safe(echo_server):
    """`-F name=value` treats `;`, `@` and a leading `<` specially; reading
    the value from a file avoids that whole class of surprise."""
    payload = "a@b;c<d,e"
    curl_client.post(echo_server.url, "u", "p", fields={"content": payload})
    assert payload.encode() in echo_server.captured["body"]


def test_temporary_field_files_are_cleaned_up(echo_server, tmp_path, monkeypatch):
    """Every forwarded form field spools to a temp file; leaking one per
    request would fill /tmp on a busy relay."""
    import tempfile as tempfile_module

    seen: list[str] = []
    real_mkstemp = tempfile_module.mkstemp

    def tracking_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        seen.append(path)
        return fd, path

    monkeypatch.setattr(curl_client.tempfile, "mkstemp", tracking_mkstemp)
    curl_client.post(echo_server.url, "u", "p", fields={"a": "1", "b": "2"})

    from pathlib import Path

    assert seen, "expected spooled field files"
    assert not [p for p in seen if Path(p).exists()]

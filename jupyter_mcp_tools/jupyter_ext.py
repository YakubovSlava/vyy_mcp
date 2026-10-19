"""Jupyter Server extension: fake the JupyterHub-style
``/user/<username>/proxy/<port>`` URL shape on a standalone (non-Hub)
JupyterLab, by 302-redirecting it to the real ``/proxy/<port>`` that
``jupyter-server-proxy`` already serves.

Only exists because this deployment runs plain ``jupyter-lab`` (no
JupyterHub in front of it), so there's no real ``/user/<username>/``
routing layer — see PLAN.md. The ``<username>`` segment is accepted and
ignored; it's cosmetic only, not an identity check.

**Do not enable this on a real JupyterHub.** There, ``/user/<name>/`` is
a genuine routing prefix the Hub's own proxy already understands — this
extension is unnecessary noise at best there, since the request never
reaches this handler with that prefix intact (the Hub strips it before
forwarding to the single-user server in the first place).

Enable it in jupyter_server_config.py (no entry-point/install step
needed, just the dotted module path):

    c.ServerApp.jpserver_extensions = {"jupyter_mcp_tools.jupyter_ext": True}

Then restart jupyter-lab.
"""
from __future__ import annotations

from tornado.web import RequestHandler


class _FakeUserPrefixRedirectHandler(RequestHandler):
    def get(self, _username: str, rest: str) -> None:
        target = f"/proxy/{rest}"
        if self.request.query:
            target += f"?{self.request.query}"
        self.redirect(target, permanent=False)


def _jupyter_server_extension_points():
    return [{"module": "jupyter_mcp_tools.jupyter_ext"}]


def _load_jupyter_server_extension(server_app) -> None:
    server_app.web_app.add_handlers(
        ".*$",
        [(r"/user/([^/]+)/proxy/(.*)", _FakeUserPrefixRedirectHandler)],
    )
    server_app.log.info(
        "jupyter_mcp_tools.jupyter_ext: redirecting /user/<username>/proxy/<port> -> /proxy/<port>"
    )

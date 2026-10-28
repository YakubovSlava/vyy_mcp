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
    """Only `get()` existed here originally — fine for loading the admin
    UI's pages, but a POST (any form submit: upload, delete, the "⚙"
    dialog's save/reset buttons, ...) reaching this fake prefix instead
    of the real `/proxy/<port>/...` had no handler at all, since Tornado's
    `RequestHandler` defaults every other verb to `_unimplemented_method`
    (a 405). A 302/303 redirect would also silently drop the POST body
    (browsers resubmit those as GET) even if it didn't error — so every
    verb below redirects with 307, which preserves both the method and
    the body.
    """

    def check_xsrf_cookie(self) -> None:
        """No-op: this handler never reads or changes any state — it
        only computes a redirect target from the URL path — so an XSRF
        token has nothing to protect here. Tornado's `xsrf_cookies`
        setting (which `ServerApp` always turns on) otherwise requires
        one for every non-GET request, which a plain `curl -X POST`/form
        submit through this fake prefix would never have, surfacing as a
        403 for every verb added above."""

    def _redirect(self, rest: str) -> None:
        target = f"/proxy/{rest}"
        if self.request.query:
            target += f"?{self.request.query}"
        self.redirect(target, status=307)

    def get(self, _username: str, rest: str) -> None:
        target = f"/proxy/{rest}"
        if self.request.query:
            target += f"?{self.request.query}"
        self.redirect(target, permanent=False)

    def post(self, _username: str, rest: str) -> None:
        self._redirect(rest)

    def put(self, _username: str, rest: str) -> None:
        self._redirect(rest)

    def delete(self, _username: str, rest: str) -> None:
        self._redirect(rest)

    def patch(self, _username: str, rest: str) -> None:
        self._redirect(rest)


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

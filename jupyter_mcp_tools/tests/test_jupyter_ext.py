from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application

from jupyter_mcp_tools.jupyter_ext import _FakeUserPrefixRedirectHandler


class TestFakeUserPrefixRedirect(AsyncHTTPTestCase):
    def get_app(self):
        # `xsrf_cookies=True` matches jupyter_server's own ServerApp,
        # which always turns this on regardless of auth settings -- the
        # earlier version of this test suite ran against the Tornado
        # default (False) and so never actually exercised the 403 that
        # broke every POST/PUT/DELETE/PATCH test below on a real Jupyter
        # server, even after the 405 fix.
        return Application(
            [(r"/user/([^/]+)/proxy/(.*)", _FakeUserPrefixRedirectHandler)], xsrf_cookies=True
        )

    def test_get_redirects_to_real_proxy_path(self):
        response = self.fetch("/user/alice/proxy/8766/config", follow_redirects=False)
        assert response.code == 302
        assert response.headers["Location"] == "/proxy/8766/config"

    def test_get_preserves_query_string(self):
        response = self.fetch("/user/alice/proxy/8766/?message=hi", follow_redirects=False)
        assert response.code == 302
        assert response.headers["Location"] == "/proxy/8766/?message=hi"

    def test_post_redirects_with_307_preserving_method_and_location(self):
        # A plain 302/303 would make browsers resubmit this as a GET,
        # silently dropping the form body -- this is exactly the gap that
        # broke the admin UI's POST actions (upload/delete/the settings
        # dialog's buttons) when reached through the fake /user/<name>/
        # proxy/<port> prefix instead of the real /proxy/<port> path.
        response = self.fetch(
            "/user/alice/proxy/8766/config/instructions/reset",
            method="POST",
            body="",
            follow_redirects=False,
        )
        assert response.code == 307
        assert response.headers["Location"] == "/proxy/8766/config/instructions/reset"

    def test_put_and_delete_and_patch_also_redirect_with_307(self):
        for method in ("PUT", "DELETE", "PATCH"):
            kwargs = {"body": ""} if method in ("PUT", "PATCH") else {}
            response = self.fetch(
                "/user/alice/proxy/8766/tools/greet",
                method=method,
                follow_redirects=False,
                **kwargs,
            )
            assert response.code == 307, method
            assert response.headers["Location"] == "/proxy/8766/tools/greet", method

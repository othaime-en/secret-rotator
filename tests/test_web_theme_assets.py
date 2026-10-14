"""Tests for the design-system foundation: self-hosted fonts, theme assets
and the restyled sign-in page."""

import os
import unittest

from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter


class TestThemeAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_env = {
            k: os.environ.pop(k, None) for k in ("FLASK_SECRET_KEY", "SECRET_ROTATOR_ENV")
        }
        os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def setUp(self):
        DistributedLock.reset_local_locks()
        app = create_app(RotationEngine())
        app.config["TESTING"] = True
        self.client = app.test_client()
        limiter.reset()

    def test_static_assets_are_served_without_auth(self):
        for path in (
            "/static/css/tokens.css",
            "/static/css/base.css",
            "/static/css/components.css",
            "/static/css/auth.css",
            "/static/js/theme.js",
            "/static/fonts/geist-latin-wght-normal.woff2",
            "/static/fonts/geist-mono-latin-wght-normal.woff2",
        ):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)
            resp.close()

    def test_tokens_define_light_dark_and_forced_themes(self):
        resp = self.client.get("/static/css/tokens.css")
        css = resp.get_data(as_text=True)
        resp.close()
        self.assertIn("prefers-color-scheme: dark", css)
        self.assertIn(':root[data-theme="dark"]', css)
        self.assertIn(':root:not([data-theme="light"])', css)

    def test_fonts_are_self_hosted_not_from_a_cdn(self):
        resp = self.client.get("/static/css/tokens.css")
        css = resp.get_data(as_text=True)
        resp.close()
        self.assertNotIn("fonts.googleapis.com", css)
        self.assertNotIn("http://", css)
        self.assertNotIn("https://", css)

    def test_login_page_uses_new_design_system(self):
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("css/tokens.css", html)
        self.assertIn("js/theme.js", html)
        self.assertIn('name="csrf_token"', html)
        self.assertNotIn("googleapis", html)
        # The legacy dashboard bundle must not load on the sign-in page.
        self.assertNotIn("dashboard.js", html)

    def test_theme_script_loads_before_stylesheets(self):
        html = self.client.get("/login").get_data(as_text=True)
        self.assertLess(html.index("js/theme.js"), html.index("css/tokens.css"))


if __name__ == "__main__":
    unittest.main()

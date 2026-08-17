import os
import re
import unittest

from werkzeug.security import generate_password_hash

from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

TEST_USERNAME = "testadmin"
TEST_PASSWORD = "correct-horse-battery-staple"

CSRF_META_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)">')


class TestCSRFProtection(unittest.TestCase):
    """The login form had no CSRF protection - that meant any authenticated admin's
    browser could be tricked by a malicious page into rotating or
    restoring secrets without their knowledge."""

    @classmethod
    def setUpClass(cls):
        cls._saved_env = {
            k: os.environ.pop(k, None)
            for k in (
                "SECRET_ROTATOR_ADMIN_USERNAME",
                "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
                "FLASK_SECRET_KEY",
                "SECRET_ROTATOR_ENV",
            )
        }
        os.environ["SECRET_ROTATOR_ADMIN_USERNAME"] = TEST_USERNAME
        os.environ["SECRET_ROTATOR_ADMIN_PASSWORD_HASH"] = generate_password_hash(
            TEST_PASSWORD
        )
        os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def setUp(self):
        engine = RotationEngine()
        app = create_app(engine)
        app.config["TESTING"] = True
        # CSRF stays ON for this file (that's the point). WTF_CSRF_SSL_STRICT
        # only matters for HTTPS requests, which the Flask test client
        # doesn't send, so it doesn't interfere here.
        self.client = app.test_client()
        # See test_auth.py's setUp for why this is necessary: the rate
        # limiter (S10) is a shared singleton across every test in this
        # process, and this file alone makes enough /login and
        # /api/rotate requests to trip real limits without a reset.
        limiter.reset()

    def _get_csrf_token(self):
        """Fetch a real CSRF token the way a browser would: load a page
        and read the token out of the <meta> tag Flask-WTF renders."""
        resp = self.client.get("/login")
        match = CSRF_META_RE.search(resp.get_data(as_text=True))
        self.assertIsNotNone(match, "csrf-token meta tag not found on /login")
        return match.group(1)

    def _login(self):
        token = self._get_csrf_token()
        resp = self.client.post(
            "/login",
            data={
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD,
                "csrf_token": token,
            },
        )
        self.assertEqual(resp.status_code, 302)

    # ---- login form ----

    def test_login_without_csrf_token_is_rejected(self):
        resp = self.client.post(
            "/login", data={"username": TEST_USERNAME, "password": TEST_PASSWORD}
        )
        self.assertEqual(resp.status_code, 400)

    def test_login_with_valid_csrf_token_succeeds(self):
        token = self._get_csrf_token()
        resp = self.client.post(
            "/login",
            data={
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD,
                "csrf_token": token,
            },
        )
        self.assertEqual(resp.status_code, 302)

    # ---- authenticated API POSTs ----

    def test_rotate_without_csrf_token_is_rejected(self):
        self._login()
        resp = self.client.post("/api/rotate")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("CSRF", resp.get_json().get("error", ""))

    def test_rotate_with_csrf_header_succeeds(self):
        self._login()
        token = self._get_csrf_token()
        resp = self.client.post(
            "/api/rotate", headers={"X-CSRFToken": token}
        )
        # 202 Accepted: /api/rotate now starts a background job rather
        # than rotating synchronously (roadmap Phase 2). The point of
        # this test is that a valid CSRF token gets past CSRF checks,
        # not the exact success status — 202 vs the old 200 either way
        # confirms CSRF didn't reject it (that's a 400, tested above).
        self.assertEqual(resp.status_code, 202)

    def test_restore_without_csrf_token_is_rejected(self):
        self._login()
        resp = self.client.post("/api/restore", json={"backup_file": "x.json"})
        self.assertEqual(resp.status_code, 400)

    def test_restore_with_csrf_header_is_not_a_csrf_failure(self):
        self._login()
        token = self._get_csrf_token()
        resp = self.client.post(
            "/api/restore",
            json={"backup_file": "does-not-exist.json"},
            headers={"X-CSRFToken": token},
        )
        # Token is valid, so this should get past CSRF and fail for a
        # normal reason (file not found), not "CSRF validation failed".
        self.assertNotEqual(resp.status_code, 400)
        self.assertEqual(resp.status_code, 404)

    def test_run_verification_without_csrf_token_is_rejected(self):
        self._login()
        resp = self.client.post("/api/run-verification")
        self.assertEqual(resp.status_code, 400)

    # ---- GET requests are unaffected ----

    def test_get_requests_do_not_require_csrf_token(self):
        self._login()
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)

    def test_stale_token_is_rejected(self):
        self._login()
        resp = self.client.post(
            "/api/rotate", headers={"X-CSRFToken": "not-a-real-token"}
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
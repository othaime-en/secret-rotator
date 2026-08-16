import os
import unittest

from werkzeug.security import generate_password_hash

from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

TEST_USERNAME = "testadmin"
TEST_PASSWORD = "correct-horse-battery-staple"


class TestRateLimiting(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._saved_env = {
            k: os.environ.pop(k, None)
            for k in (
                "SECRET_ROTATOR_ADMIN_USERNAME",
                "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
                "FLASK_SECRET_KEY",
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
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()
        # Every test in this file needs a clean counter, both against
        # bleed from other test files and between tests in this file.
        limiter.reset()

    def _login(self):
        return self.client.post(
            "/login", data={"username": TEST_USERNAME, "password": TEST_PASSWORD}
        )

    def test_login_is_rate_limited_after_repeated_attempts(self):
        """10 attempts/5min is configured for /login. The 11th request
        in the window should be rejected with 429.

        Uses a wrong password so the session never becomes
        authenticated — rate_limit_key() keys authenticated requests
        by username rather than IP (see web/rate_limit.py), so a
        succeeding login partway through would otherwise split these
        requests across two different buckets. A real brute-force
        attempt is exactly this shape anyway: repeated failed guesses
        against one anonymous, IP-keyed bucket.
        """
        def _bad_login():
            return self.client.post(
                "/login", data={"username": TEST_USERNAME, "password": "wrong"}
            )

        responses = [_bad_login() for _ in range(10)]
        for resp in responses:
            self.assertNotEqual(resp.status_code, 429)

        eleventh = _bad_login()
        self.assertEqual(eleventh.status_code, 429)

    def test_rotate_is_rate_limited_after_repeated_calls(self):
        """/api/rotate is limited to 10/minute per user. Each call
        starts (or attaches to) a background job rather than running
        synchronously, so this only exercises the enqueue endpoint,
        not a full rotation sweep."""
        self._login()

        for _ in range(10):
            resp = self.client.post("/api/rotate")
            self.assertNotEqual(resp.status_code, 429)

        eleventh = self.client.post("/api/rotate")
        self.assertEqual(eleventh.status_code, 429)

    def test_healthz_is_exempt_from_rate_limiting(self):
        """Docker/orchestrator healthchecks poll this every ~30s
        indefinitely and must never be throttled."""
        for _ in range(50):
            resp = self.client.get("/api/healthz")
            self.assertEqual(resp.status_code, 200)

    def test_request_body_over_limit_is_rejected(self):
        """MAX_CONTENT_LENGTH (1MB) should reject oversized bodies
        before they're processed."""
        self._login()
        oversized_payload = "x" * (2 * 1024 * 1024)  # 2MB
        resp = self.client.post(
            "/api/restore",
            data=oversized_payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 413)


if __name__ == "__main__":
    unittest.main()
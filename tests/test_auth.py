import os
import unittest

from werkzeug.security import generate_password_hash

from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app

TEST_USERNAME = "testadmin"
TEST_PASSWORD = "correct-horse-battery-staple"


class TestAuthEnforcement(unittest.TestCase):
    """Regression tests for S1: the dashboard and every API route were
    reachable with zero authentication."""

    @classmethod
    def setUpClass(cls):
        # Configure credentials via env vars (highest priority in
        # web/auth.py's resolution order) so we don't touch the shared
        # on-disk settings singleton.
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
        self.client = app.test_client()

    def _login(self, username=TEST_USERNAME, password=TEST_PASSWORD):
        return self.client.post(
            "/login", data={"username": username, "password": password}
        )

    # ---- unauthenticated access ----

    def test_dashboard_redirects_to_login_when_unauthenticated(self):
        resp = self.client.get("/", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_api_status_returns_401_when_unauthenticated(self):
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("error", resp.get_json())

    def test_api_rotate_returns_401_when_unauthenticated(self):
        resp = self.client.post("/api/rotate")
        self.assertEqual(resp.status_code, 401)

    def test_api_restore_returns_401_when_unauthenticated(self):
        resp = self.client.post("/api/restore", json={"backup_file": "x.json"})
        self.assertEqual(resp.status_code, 401)

    def test_api_backups_returns_401_when_unauthenticated(self):
        resp = self.client.get("/api/backups")
        self.assertEqual(resp.status_code, 401)

    def test_healthz_is_reachable_without_auth(self):
        resp = self.client.get("/api/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"status": "ok"})

    def test_login_page_itself_is_reachable(self):
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)

    # ---- login flow ----

    def test_login_with_correct_credentials_succeeds(self):
        resp = self._login()
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/", resp.headers["Location"])

    def test_login_with_wrong_password_fails(self):
        resp = self._login(password="wrong-password")
        self.assertEqual(resp.status_code, 200)  # re-renders login form
        self.assertIn(b"Invalid username or password", resp.data)

    def test_login_with_wrong_username_fails(self):
        resp = self._login(username="not-the-admin")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Invalid username or password", resp.data)

    def test_authenticated_session_can_reach_dashboard(self):
        self._login()
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_session_can_reach_api(self):
        self._login()
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "running")

    def test_logout_revokes_session(self):
        self._login()
        self.assertEqual(self.client.get("/api/status").status_code, 200)

        self.client.get("/logout")

        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 401)

    def test_login_redirect_only_follows_relative_next(self):
        """Guard against the 'next' param being used as an open redirect."""
        resp = self.client.post(
            "/login",
            data={
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD,
                "next": "//evil.example.com/",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("evil.example.com", resp.headers["Location"])


class TestAuthNotConfigured(unittest.TestCase):
    """When no credentials are configured at all, login must always fail
    closed (nobody can get in) rather than open (anyone can get in)."""

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
        os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_login_fails_closed_when_unconfigured(self):
        engine = RotationEngine()
        app = create_app(engine)
        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.post(
            "/login", data={"username": "admin", "password": "anything"}
        )
        self.assertEqual(resp.status_code, 200)

        # Confirm no session was established.
        self.assertEqual(client.get("/api/status").status_code, 401)


if __name__ == "__main__":
    unittest.main()
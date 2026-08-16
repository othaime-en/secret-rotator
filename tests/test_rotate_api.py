import os
import time
import unittest

from werkzeug.security import generate_password_hash

from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

TEST_USERNAME = "testadmin"
TEST_PASSWORD = "correct-horse-battery-staple"


def _wait_until(predicate, timeout=5, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestRotateEndpoint(unittest.TestCase):
    """Integration tests for the async POST /api/rotate + GET
    /api/rotate/<job_id> pair (roadmap Phase 2: "background jobs for
    /api/rotate"). RotationEngine here has zero configured jobs, so a
    "rotation" completes almost instantly — these tests are about the
    HTTP contract (status codes, job lifecycle, polling), not actual
    secret rotation, which is covered in test_rotation_jobs.py and
    test_rotation_engine.py."""

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
        limiter.reset()
        self._login()

    def _login(self):
        return self.client.post(
            "/login", data={"username": TEST_USERNAME, "password": TEST_PASSWORD}
        )

    def test_post_rotate_returns_202_with_job_id(self):
        resp = self.client.post("/api/rotate")
        self.assertEqual(resp.status_code, 202)

        body = resp.get_json()
        self.assertIn("job_id", body)
        self.assertIn(body["status"], ("queued", "running", "completed"))
        self.assertEqual(body["status_url"], f"/api/rotate/{body['job_id']}")

    def test_post_rotate_does_not_block_the_request(self):
        """The whole point: the response comes back fast regardless of
        how long rotation takes, because it isn't run inline."""
        started = time.monotonic()
        resp = self.client.post("/api/rotate")
        elapsed = time.monotonic() - started

        self.assertEqual(resp.status_code, 202)
        self.assertLess(elapsed, 1.0, "POST /api/rotate should return immediately")

    def test_job_can_be_polled_to_completion(self):
        job_id = self.client.post("/api/rotate").get_json()["job_id"]

        completed = _wait_until(
            lambda: self.client.get(f"/api/rotate/{job_id}").get_json()["status"]
            == "completed"
        )
        self.assertTrue(completed)

        final = self.client.get(f"/api/rotate/{job_id}").get_json()
        self.assertEqual(final["job_id"], job_id)
        self.assertIsNone(final["error"])

    def test_unknown_job_id_returns_404(self):
        resp = self.client.get("/api/rotate/not-a-real-job-id")
        self.assertEqual(resp.status_code, 404)

    def test_status_endpoint_requires_auth(self):
        # Fresh, never-logged-in client (self.client already has an
        # authenticated session from setUp).
        from secret_rotator.rotation_engine import RotationEngine as _RE
        from secret_rotator.web.app import create_app as _create_app

        app = _create_app(_RE())
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        client = app.test_client()

        resp = client.get("/api/rotate/some-job-id")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
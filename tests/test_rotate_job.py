"""Tests for rotating a single job: RotationEngine.rotate_job and
POST /api/jobs/<name>/rotate."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from werkzeug.security import generate_password_hash

from secret_rotator.audit_log import AuditLog
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.job_status import JobStatusService
from secret_rotator.providers.base import SecretProvider
from secret_rotator.rotation_engine import RotationEngine, RotationInProgressError
from secret_rotator.rotators.base import SecretRotator
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

USERNAME = "rotate-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)


class FakeProvider(SecretProvider):
    def __init__(self):
        super().__init__(name="p", config={})
        self.store = {"db": "old", "api": "old"}
        self.fail = False

    def get_secret(self, secret_id):
        return self.store[secret_id]

    def update_secret(self, secret_id, new_value):
        if self.fail:
            return False
        self.store[secret_id] = new_value
        return True

    def validate_connection(self):
        return True


class FakeRotator(SecretRotator):
    def __init__(self):
        super().__init__(name="r", config={})

    def generate_new_secret(self):
        return "new-value"

    def validate_secret(self, value):
        return True


class EngineFixture(unittest.TestCase):
    def setUp(self):
        DistributedLock.reset_local_locks()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.audit = AuditLog(audit_file=str(Path(tmp.name) / "audit.log"))
        patcher = patch("secret_rotator.rotation_engine.audit_log", self.audit)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.provider = FakeProvider()
        self.engine = RotationEngine()
        self.engine.backup_manager = MagicMock()
        self.engine.register_provider(self.provider)
        self.engine.register_rotator(FakeRotator())
        self.engine.rotation_jobs = [
            {"name": "db_job", "provider": "p", "rotator": "r", "secret_id": "db"},
            {"name": "api_job", "provider": "p", "rotator": "r", "secret_id": "api"},
        ]


class TestEngineRotateJob(EngineFixture):
    def test_rotates_only_the_named_job(self):
        self.assertTrue(self.engine.rotate_job("db_job", actor="alice"))
        self.assertEqual(self.provider.store["db"], "new-value")
        self.assertEqual(self.provider.store["api"], "old")

    def test_records_the_actor_in_the_audit_log(self):
        self.engine.rotate_job("db_job", actor="alice")
        event = self.audit.get_recent_events(action="rotate")[0]
        self.assertEqual(event["actor"], "alice")
        self.assertEqual(event["details"]["job"], "db_job")
        self.assertTrue(event["success"])

    def test_failed_rotation_returns_false(self):
        self.provider.fail = True
        self.assertFalse(self.engine.rotate_job("db_job"))

    def test_unknown_job_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.engine.rotate_job("nope")

    def test_refused_while_a_rotation_holds_the_lock(self):
        self.engine._rotation_lock.acquire()
        try:
            with self.assertRaises(RotationInProgressError):
                self.engine.rotate_job("db_job")
        finally:
            self.engine._rotation_lock.release()
        self.assertEqual(self.provider.store["db"], "old")

    def test_lock_is_released_afterwards(self):
        self.engine.rotate_job("db_job")
        self.assertFalse(self.engine.is_rotation_in_progress())
        self.assertTrue(self.engine.rotate_job("api_job"))

    def test_lock_is_released_even_when_rotation_fails(self):
        self.provider.fail = True
        self.engine.rotate_job("db_job")
        self.assertFalse(self.engine.is_rotation_in_progress())

    def test_sweep_is_refused_while_a_single_rotation_runs(self):
        seen = []

        def during_rotation():
            try:
                self.engine.rotate_all_secrets()
            except RotationInProgressError:
                seen.append("refused")

        original = self.provider.get_secret
        self.provider.get_secret = lambda sid: (during_rotation(), original(sid))[1]
        self.engine.rotate_job("db_job")
        self.assertEqual(seen, ["refused"])


class TestRotateJobApi(EngineFixture):
    @classmethod
    def setUpClass(cls):
        cls._saved = {k: os.environ.pop(k, None) for k in ENV_KEYS}
        os.environ["SECRET_ROTATOR_ADMIN_USERNAME"] = USERNAME
        os.environ["SECRET_ROTATOR_ADMIN_PASSWORD_HASH"] = generate_password_hash(PASSWORD)
        os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    @classmethod
    def tearDownClass(cls):
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        for key, value in cls._saved.items():
            if value is not None:
                os.environ[key] = value

    def setUp(self):
        super().setUp()
        app = create_app(self.engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.job_status = JobStatusService(self.engine, audit=self.audit)
        self.client = app.test_client()
        limiter.reset()

    def login(self):
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def test_requires_login(self):
        resp = self.client.post("/api/jobs/db_job/rotate")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self.provider.store["db"], "old")

    def test_get_is_not_allowed(self):
        self.login()
        self.assertEqual(self.client.get("/api/jobs/db_job/rotate").status_code, 405)

    def test_rotates_and_returns_the_updated_job(self):
        self.login()
        resp = self.client.post("/api/jobs/db_job/rotate")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["job"]["name"], "db_job")
        self.assertEqual(data["job"]["status"], "current")
        self.assertIsNotNone(data["job"]["last_rotated"])
        self.assertEqual(self.provider.store["db"], "new-value")

    def test_records_the_logged_in_user_as_actor(self):
        self.login()
        self.client.post("/api/jobs/db_job/rotate")
        self.assertEqual(self.audit.get_recent_events(action="rotate")[0]["actor"], USERNAME)

    def test_failed_rotation_is_200_with_success_false(self):
        self.login()
        self.provider.fail = True
        resp = self.client.post("/api/jobs/db_job/rotate")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertEqual(data["job"]["status"], "failed")
        self.assertIn("update_secret", data["job"]["last_error"])

    def test_job_names_containing_slashes_work(self):
        self.login()
        self.engine.rotation_jobs.append(
            {"name": "prod/db", "provider": "p", "rotator": "r", "secret_id": "db"}
        )
        resp = self.client.post("/api/jobs/prod%2Fdb/rotate")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["job"]["name"], "prod/db")

    def test_unknown_job_is_404(self):
        self.login()
        resp = self.client.post("/api/jobs/nope/rotate")
        self.assertEqual(resp.status_code, 404)

    def test_rotation_in_progress_is_409(self):
        self.login()
        self.engine._rotation_lock.acquire()
        try:
            resp = self.client.post("/api/jobs/db_job/rotate")
        finally:
            self.engine._rotation_lock.release()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.provider.store["db"], "old")

    def test_is_rate_limited(self):
        self.login()
        codes = [self.client.post("/api/jobs/db_job/rotate").status_code for _ in range(31)]
        self.assertEqual(codes[-1], 429)
        self.assertNotIn(429, codes[:-1])


class TestJobSummary(EngineFixture):
    def test_job_summary_by_name(self):
        service = JobStatusService(self.engine, audit=self.audit)
        self.assertEqual(service.job_summary("api_job")["name"], "api_job")
        self.assertIsNone(service.job_summary("nope"))


if __name__ == "__main__":
    unittest.main()

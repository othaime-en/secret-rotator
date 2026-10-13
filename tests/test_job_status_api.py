"""Tests for the /api/job-status and /api/overview endpoints."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from werkzeug.security import generate_password_hash

from secret_rotator.audit_log import AuditLog
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.job_status import JobStatusService
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

USERNAME = "status-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)


class TestStatusApi(unittest.TestCase):
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
        DistributedLock.reset_local_locks()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.audit_path = Path(tmp.name) / "audit.log"

        engine = RotationEngine()
        engine.backup_manager = MagicMock()
        engine.backup_manager.count_backups.return_value = 7
        engine.rotation_jobs = [
            {"name": "db_job", "secret_id": "db", "provider": "p", "rotator": "r"}
        ]

        app = create_app(engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.job_status = JobStatusService(engine, audit=AuditLog(audit_file=str(self.audit_path)))
        self.client = app.test_client()
        limiter.reset()

        checker = MagicMock()
        checker.get_backup_health_metrics.return_value = {"status": "unknown"}
        checker.get_verification_history.return_value = []
        patcher = patch("secret_rotator.job_status.BackupIntegrityChecker", return_value=checker)
        patcher.start()
        self.addCleanup(patcher.stop)

    def login(self):
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def write_event(self, success, reason=None):
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "rotate",
            "actor": "system",
            "secret_id": "db",
            "success": success,
            "details": {"job": "db_job", **({"reason": reason} if reason else {})},
        }
        self.audit_path.write_text(json.dumps(event) + "\n")

    def test_endpoints_require_login(self):
        for url in ("/api/job-status", "/api/overview"):
            resp = self.client.get(url)
            self.assertIn(resp.status_code, (302, 401), url)

    def test_job_status_lists_jobs(self):
        self.login()
        self.write_event(success=False, reason="timeout")
        data = self.client.get("/api/job-status").get_json()
        self.assertEqual(len(data["jobs"]), 1)
        job = data["jobs"][0]
        self.assertEqual(job["name"], "db_job")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["last_error"], "timeout")

    def test_overview_shape(self):
        self.login()
        self.write_event(success=False, reason="timeout")
        data = self.client.get("/api/overview").get_json()
        self.assertEqual(
            set(data),
            {"summary", "attention", "jobs", "recent_activity", "verification"},
        )
        self.assertEqual(data["summary"], {"jobs": 1, "backups": 7, "rotating": 0})
        self.assertEqual(data["attention"][0]["job"], "db_job")
        self.assertEqual(data["recent_activity"][0]["message"], "db_job failed")
        self.assertEqual(data["verification"]["status"], "unknown")

    def test_responses_never_contain_secret_values(self):
        self.login()
        self.write_event(success=True)
        body = self.client.get("/api/overview").get_data(as_text=True)
        for needle in ("old_value", "new_value", "password"):
            self.assertNotIn(needle, body)

    def test_existing_jobs_endpoint_is_unchanged(self):
        self.login()
        data = self.client.get("/api/jobs").get_json()
        self.assertEqual(data["jobs"][0]["name"], "db_job")
        self.assertNotIn("status", data["jobs"][0])


if __name__ == "__main__":
    unittest.main()

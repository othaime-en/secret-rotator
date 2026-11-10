"""Tests for rotation duration in audit events, rotator display safety,
the exact-match backup filter, JobStatusService.job_detail and
GET /api/jobs/<name>/detail."""

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from werkzeug.security import generate_password_hash

from secret_rotator.audit_log import AuditLog
from secret_rotator.backup_manager import BackupManager
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.job_status import MAX_REASON_LENGTH, JobStatusService
from secret_rotator.providers.base import SecretProvider
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.rotator_display import describe_rotator
from secret_rotator.rotators.advanced_rotators import (
    APIKeyRotator,
    CertificateRotator,
    DatabasePasswordRotator,
    JWTSecretRotator,
    OAuth2TokenRotator,
    SSHKeyRotator,
)
from secret_rotator.rotators.base import SecretRotator
from secret_rotator.rotators.password_rotator import PasswordRotator
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ts(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat()


class SlowProvider(SecretProvider):
    def __init__(self, delay=0.0, ok=True):
        super().__init__(name="p", config={})
        self.delay = delay
        self.ok = ok

    def get_secret(self, secret_id):
        return "old"

    def update_secret(self, secret_id, new_value):
        time.sleep(self.delay)
        return self.ok

    def validate_connection(self):
        return True


class FakeRotator(SecretRotator):
    def __init__(self):
        super().__init__(name="r", config={})

    def generate_new_secret(self):
        return "new"

    def validate_secret(self, value):
        return True


class TestRotationDuration(unittest.TestCase):
    def setUp(self):
        DistributedLock.reset_local_locks()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.audit = AuditLog(audit_file=str(Path(tmp.name) / "audit.log"))
        patcher = patch("secret_rotator.rotation_engine.audit_log", self.audit)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = RotationEngine()
        self.engine.backup_manager = MagicMock()
        self.provider = SlowProvider()
        self.engine.register_provider(self.provider)
        self.engine.register_rotator(FakeRotator())
        self.job = {"name": "j", "provider": "p", "rotator": "r", "secret_id": "s"}

    def last_event(self):
        return self.audit.get_recent_events(action="rotate")[-1]

    def test_success_records_a_duration(self):
        self.provider.delay = 0.05
        self.assertTrue(self.engine.rotate_secret(self.job))
        details = self.last_event()["details"]
        self.assertIsInstance(details["duration_ms"], int)
        self.assertGreaterEqual(details["duration_ms"], 50)
        self.assertEqual(details["provider"], "p")

    def test_failure_records_a_duration_and_keeps_the_reason(self):
        self.provider.ok = False
        self.engine.rotate_secret(self.job)
        details = self.last_event()["details"]
        self.assertIn("duration_ms", details)
        self.assertEqual(details["reason"], "provider update_secret returned False")

    def test_early_failures_record_a_duration_too(self):
        self.engine.rotate_secret({**self.job, "provider": "missing"})
        details = self.last_event()["details"]
        self.assertIn("not found", details["reason"])
        self.assertIn("duration_ms", details)

    def test_each_rotation_is_timed_separately(self):
        self.provider.delay = 0.2
        self.engine.rotate_secret(self.job)
        self.provider.delay = 0.0
        self.engine.rotate_secret(self.job)
        events = self.audit.get_recent_events(action="rotate")
        self.assertGreaterEqual(events[0]["details"]["duration_ms"], 200)
        self.assertLess(events[1]["details"]["duration_ms"], 150)


class TestDescribeRotator(unittest.TestCase):
    def values(self, rotator):
        return {s["label"]: s["value"] for s in describe_rotator(rotator)["settings"]}

    def test_password(self):
        rotator = PasswordRotator(
            "p", {"length": 24, "use_symbols": False, "exclude_ambiguous": True}
        )
        described = describe_rotator(rotator)
        self.assertEqual(described["type"], "Password")
        self.assertEqual(
            self.values(rotator),
            {"Length": "24", "Characters": "a-z A-Z 0-9", "Skip look-alikes": "Yes"},
        )

    def test_password_defaults(self):
        values = self.values(PasswordRotator("p", {}))
        self.assertEqual(values["Characters"], "a-z A-Z 0-9 symbols")
        self.assertEqual(values["Skip look-alikes"], "No")

    def test_api_key(self):
        values = self.values(
            APIKeyRotator("a", {"length": 32, "format": "hex", "prefix": "sk_live_"})
        )
        self.assertEqual(values, {"Length": "32", "Format": "hex", "Checksum": "No"})

    def test_other_builtin_rotators(self):
        self.assertEqual(
            self.values(JWTSecretRotator("j", {"algorithm": "HS512"})), {"Algorithm": "HS512"}
        )
        self.assertEqual(
            self.values(SSHKeyRotator("s", {"key_type": "ed25519", "key_size": 4096})),
            {"Key type": "ed25519", "Key size": "4096"},
        )
        self.assertEqual(
            self.values(CertificateRotator("c", {"validity_days": 90})),
            {"Validity (days)": "90", "Key size": "2048"},
        )
        self.assertEqual(self.values(OAuth2TokenRotator("o", {"length": 40})), {"Length": "40"})
        self.assertEqual(describe_rotator(JWTSecretRotator("j", {}))["type"], "JWT secret")

    def test_connection_details_are_never_shown(self):
        rotator = DatabasePasswordRotator(
            "d",
            {
                "db_type": "postgresql",
                "host": "db.internal.example.com",
                "port": 5432,
                "database": "payments",
                "username": "svc_rotator",
                "password": "hunter2",
                "length": 40,
            },
        )
        described = describe_rotator(rotator)
        self.assertEqual(described["type"], "Database")
        self.assertEqual(self.values(rotator), {"Database type": "postgresql", "Length": "40"})
        dumped = json.dumps(described)
        for hidden in ("db.internal", "payments", "svc_rotator", "hunter2", "5432"):
            self.assertNotIn(hidden, dumped)

    def test_oauth_client_id_is_never_shown(self):
        rotator = OAuth2TokenRotator("o", {"client_id": "client-abc-123", "provider": "github"})
        self.assertNotIn("client-abc-123", json.dumps(describe_rotator(rotator)))

    def test_unknown_rotators_show_nothing(self):
        rotator = FakeRotator()
        rotator.config = {"api_token": "SUPER-SECRET"}
        described = describe_rotator(rotator)
        self.assertEqual(described, {"type": "FakeRotator", "settings": []})

    def test_missing_rotator(self):
        self.assertEqual(describe_rotator(None), {"type": None, "settings": []})


class TestExactSecretFilter(unittest.TestCase):
    def test_exact_secret_id_is_not_a_substring_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            for secret_id in ("db", "db_password", "old_db"):
                (Path(tmp) / f"{secret_id}_20261001_040000_000000.json").write_text(
                    json.dumps(
                        {
                            "secret_id": secret_id,
                            "encrypted": True,
                            "backup_created": "2026-10-01T04:00:00",
                        }
                    )
                )
            manager = BackupManager(backup_dir=tmp, encrypt_backups=False)
            self.assertEqual(manager.list_backup_page(query="db")["total"], 3)
            exact = manager.list_backup_page(secret_id="db")
            self.assertEqual(exact["total"], 1)
            self.assertEqual(exact["backups"][0]["secret_id"], "db")
            self.assertEqual(manager.list_backup_page(secret_id="nope")["total"], 0)


class Fixture(unittest.TestCase):
    def setUp(self):
        DistributedLock.reset_local_locks()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.audit_path = self.tmp / "audit.log"
        self.audit = AuditLog(audit_file=str(self.audit_path))
        self.backup_dir = self.tmp / "backups"
        self.backup_dir.mkdir()

        self.engine = RotationEngine()
        self.engine.backup_manager = BackupManager(
            backup_dir=str(self.backup_dir), encrypt_backups=False
        )
        self.engine.rotators["password_gen"] = PasswordRotator("password_gen", {"length": 20})
        self.engine.rotation_jobs = [
            {
                "name": "db_job",
                "secret_id": "db_password",
                "provider": "file_storage",
                "rotator": "password_gen",
                "schedule": "weekly",
                "notification": True,
            },
            {"name": "other_job", "secret_id": "other", "provider": "p", "rotator": "missing"},
        ]
        self.service = JobStatusService(self.engine, audit=self.audit)

    def event(self, hours_ago, job="db_job", success=True, actor="system", **details):
        data = {
            "timestamp": ts(hours_ago),
            "action": "rotate",
            "actor": actor,
            "secret_id": "x",
            "success": success,
            "details": {"job": job, **details},
        }
        with open(self.audit_path, "a") as f:
            f.write(json.dumps(data) + "\n")

    def backup(self, secret_id, day):
        (self.backup_dir / f"{secret_id}_202610{day:02d}_040000_000000.json").write_text(
            json.dumps(
                {
                    "secret_id": secret_id,
                    "encrypted": True,
                    "backup_created": f"2026-10-{day:02d}T04:00:00",
                    "old_value": "OLD-SECRET",
                    "new_value": "NEW-SECRET",
                }
            )
        )


class TestJobDetail(Fixture):
    def test_unknown_job(self):
        self.assertIsNone(self.service.job_detail("nope"))

    def test_config_and_rotator(self):
        config = self.service.job_detail("db_job")["config"]
        self.assertEqual(config["secret_id"], "db_password")
        self.assertEqual(config["provider"], "file_storage")
        self.assertEqual(config["rotator_name"], "password_gen")
        self.assertEqual(config["schedule"], "weekly")
        self.assertTrue(config["notifications"])
        self.assertEqual(config["rotator"]["type"], "Password")
        self.assertEqual(config["rotator"]["settings"][0], {"label": "Length", "value": "20"})

    def test_missing_rotator_and_notifications_default(self):
        config = self.service.job_detail("other_job")["config"]
        self.assertEqual(config["rotator"], {"type": None, "settings": []})
        self.assertFalse(config["notifications"])

    def test_no_history(self):
        detail = self.service.job_detail("db_job")
        self.assertEqual(detail["runs"], [])
        self.assertEqual(
            detail["stats"],
            {"runs": 0, "succeeded": 0, "failed": 0, "success_rate": None, "backups": 0},
        )
        self.assertEqual(detail["job"]["status"], "never_rotated")

    def test_runs_stats_and_success_rate(self):
        self.event(50, duration_ms=1200)
        self.event(40, success=False, reason="timeout", duration_ms=30000)
        self.event(30, actor="admin", duration_ms=1400)
        self.event(20, job="other_job")
        detail = self.service.job_detail("db_job")
        self.assertEqual(
            detail["stats"],
            {"runs": 3, "succeeded": 2, "failed": 1, "success_rate": 66.7, "backups": 0},
        )
        runs = detail["runs"]
        self.assertEqual([r["timestamp"] for r in runs], [ts(30), ts(40), ts(50)])
        self.assertEqual(runs[0]["trigger"], "manual")
        self.assertEqual(runs[0]["actor"], "admin")
        self.assertEqual(runs[1]["trigger"], "schedule")
        self.assertEqual(
            (runs[1]["success"], runs[1]["reason"], runs[1]["duration_ms"]),
            (False, "timeout", 30000),
        )
        self.assertIsNone(runs[0]["reason"])

    def test_runs_without_a_recorded_duration(self):
        self.event(5)  # recorded before durations existed
        self.assertIsNone(self.service.job_detail("db_job")["runs"][0]["duration_ms"])

    def test_run_limit_keeps_stats_over_all_runs(self):
        for i in range(6):
            self.event(60 - i)
        detail = self.service.job_detail("db_job", run_limit=2)
        self.assertEqual(len(detail["runs"]), 2)
        self.assertEqual(detail["stats"]["runs"], 6)

    def test_long_failure_reasons_are_clipped(self):
        self.event(5, success=False, reason="x" * 1000)
        reason = self.service.job_detail("db_job")["runs"][0]["reason"]
        self.assertEqual(len(reason), MAX_REASON_LENGTH)

    def test_status_and_times_come_from_the_summary(self):
        self.event(30)
        self.event(2, success=False, reason="boom")
        job = self.service.job_detail("db_job")["job"]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["last_rotated"], ts(30))
        self.assertEqual(job["last_error"], "boom")

    def test_backups_are_this_jobs_secret_only(self):
        for day in (1, 2, 3):
            self.backup("db_password", day)
        self.backup("db", 4)
        self.backup("old_db_password", 5)
        detail = self.service.job_detail("db_job", backup_limit=2)
        self.assertEqual(detail["stats"]["backups"], 3)
        self.assertEqual(
            [b["secret_id"] for b in detail["backups"]], ["db_password", "db_password"]
        )
        self.assertEqual(detail["backups"][0]["file"], "db_password_20261003_040000_000000.json")

    def test_no_secret_values_anywhere(self):
        self.backup("db_password", 1)
        self.event(5, old_value="LEAK-OLD", new_value="LEAK-NEW")
        dumped = json.dumps(self.service.job_detail("db_job"))
        for needle in ("OLD-SECRET", "NEW-SECRET", "LEAK-OLD", "LEAK-NEW"):
            self.assertNotIn(needle, dumped)
        self.assertNotIn(str(self.backup_dir), dumped)


USERNAME = "detail-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)


class TestJobDetailApi(Fixture):
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
        app.job_status = self.service
        self.client = app.test_client()
        limiter.reset()

    def login(self):
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def test_requires_login(self):
        self.assertEqual(self.client.get("/api/jobs/db_job/detail").status_code, 401)

    def test_unknown_job_is_404(self):
        self.login()
        self.assertEqual(self.client.get("/api/jobs/nope/detail").status_code, 404)

    def test_detail(self):
        self.login()
        self.event(5, duration_ms=900)
        data = self.client.get("/api/jobs/db_job/detail").get_json()
        self.assertEqual(set(data), {"job", "config", "stats", "runs", "backups"})
        self.assertEqual(data["runs"][0]["duration_ms"], 900)

    def test_runs_parameter(self):
        self.login()
        for i in range(5):
            self.event(10 - i)
        self.assertEqual(
            len(self.client.get("/api/jobs/db_job/detail?runs=2").get_json()["runs"]), 2
        )
        for bad in ("0", "101", "x"):
            self.assertEqual(
                self.client.get(f"/api/jobs/db_job/detail?runs={bad}").status_code, 400, bad
            )

    def test_job_names_with_slashes(self):
        self.login()
        self.engine.rotation_jobs.append(
            {"name": "prod/db", "secret_id": "pdb", "provider": "p", "rotator": "r"}
        )
        resp = self.client.get("/api/jobs/prod%2Fdb/detail")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["config"]["name"], "prod/db")

    def test_rotate_route_is_unaffected(self):
        self.login()
        self.assertEqual(self.client.post("/api/jobs/nope/rotate").status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/db_job/rotate").status_code, 405)


if __name__ == "__main__":
    unittest.main()

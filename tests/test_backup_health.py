"""Tests for BackupHealthService (summary + background verification),
BackupIntegrityChecker.classify_success_rate and the /api/verification/*
endpoints."""

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from werkzeug.security import generate_password_hash

from secret_rotator.audit_log import AuditLog
from secret_rotator.backup_health import (
    MAX_FAILURES_LISTED,
    RUN_COMPLETED,
    RUN_FAILED,
    BackupHealthService,
)
from secret_rotator.backup_manager import BackupIntegrityChecker
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

NOW = datetime.now()  # the verification log stores naive local time


def stamp(days_ago=0, hours_ago=0):
    return (NOW - timedelta(days=days_ago, hours=hours_ago)).isoformat()


def run_report(total=48, failed=0, days_ago=0, corrupted=None, errors=None):
    return {
        "timestamp": stamp(days_ago=days_ago),
        "total_backups": total,
        "verified": total - failed,
        "failed": failed,
        "corrupted": corrupted or [],
        "errors": errors or [],
    }


class FakeChecker:
    def __init__(self, history=None):
        self.history = history or []  # newest first
        self.verify_calls = 0
        self.verify_gate = None
        self.verify_result = None
        self.verify_raises = None

    def get_verification_history(self, days=30):
        cutoff = NOW - timedelta(days=days)
        return [r for r in self.history if datetime.fromisoformat(r["timestamp"]) > cutoff]

    def verify_all_backups(self):
        self.verify_calls += 1
        if self.verify_gate:
            self.verify_gate.wait(5)
        if self.verify_raises:
            raise self.verify_raises
        return self.verify_result or run_report()


class ServiceFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.backup_dir = Path(tmp.name) / "backups"
        self.backup_dir.mkdir()
        self.audit = AuditLog(audit_file=str(Path(tmp.name) / "audit.log"))
        self.checker = FakeChecker()
        self.engine = SimpleNamespace(
            scheduler=SimpleNamespace(integrity_checker=self.checker),
            backup_manager=SimpleNamespace(backup_dir=self.backup_dir),
        )
        self.service = BackupHealthService(self.engine, audit=self.audit)

    def finish(self):
        thread = self.service._thread
        if thread:
            thread.join(5)
            self.assertFalse(thread.is_alive())


class TestClassify(unittest.TestCase):
    def test_thresholds(self):
        classify = BackupIntegrityChecker.classify_success_rate
        self.assertEqual(classify(100), "healthy")
        self.assertEqual(classify(99.9), "warning")
        self.assertEqual(classify(95), "warning")
        self.assertEqual(classify(94.9), "critical")
        self.assertEqual(classify(0), "critical")


class TestSummary(ServiceFixture):
    def test_no_history(self):
        data = self.service.summary()
        self.assertEqual(data["status"], "unknown")
        self.assertIsNone(data["latest"])
        self.assertEqual(data["recent_runs"], [])
        self.assertEqual(data["history"], [])
        self.assertFalse(data["running"])

    def test_latest_run_and_status(self):
        self.checker.history = [run_report(total=44, failed=3), run_report(days_ago=1)]
        data = self.service.summary()
        latest = data["latest"]
        self.assertEqual((latest["total"], latest["verified"], latest["failed"]), (44, 41, 3))
        self.assertEqual(latest["success_rate"], 93.2)
        self.assertEqual(latest["status"], "critical")
        self.assertEqual(data["status"], "critical")

    def test_status_levels(self):
        for total, failed, expected in (
            (100, 0, "healthy"),
            (100, 3, "warning"),
            (100, 10, "critical"),
        ):
            self.checker.history = [run_report(total=total, failed=failed)]
            self.assertEqual(self.service.summary()["status"], expected)

    def test_empty_run_has_unknown_status_and_no_rate(self):
        self.checker.history = [run_report(total=0)]
        data = self.service.summary()
        self.assertIsNone(data["latest"]["success_rate"])
        self.assertEqual(data["status"], "unknown")

    def test_history_respects_the_day_range_newest_first(self):
        self.checker.history = [
            run_report(days_ago=1),
            run_report(days_ago=5),
            run_report(days_ago=20),
            run_report(days_ago=60),
        ]
        self.assertEqual(len(self.service.summary(days=7)["history"]), 2)
        self.assertEqual(len(self.service.summary(days=30)["history"]), 3)
        self.assertEqual(len(self.service.summary(days=90)["history"]), 4)
        stamps = [r["timestamp"] for r in self.service.summary(days=90)["history"]]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_recent_runs_are_the_last_seven_oldest_first_regardless_of_range(self):
        self.checker.history = [
            run_report(days_ago=d, failed=3 if d == 2 else 0) for d in range(10)
        ]
        data = self.service.summary(days=1)
        self.assertEqual(len(data["recent_runs"]), 7)
        self.assertEqual(len(data["history"]), 1)
        stamps = [r["timestamp"] for r in data["recent_runs"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual([r["ok"] for r in data["recent_runs"]].count(False), 1)

    def test_timestamps_are_utc(self):
        self.checker.history = [run_report()]
        for row in (self.service.summary()["latest"], self.service.summary()["history"][0]):
            self.assertTrue(row["timestamp"].endswith("+00:00"))

    def test_failures_list_names_not_paths(self):
        report = run_report(
            total=10,
            failed=2,
            corrupted=[
                {
                    "backup_file": f"{self.backup_dir}/db_20261010_040000_000000.json",
                    "secret_id": "db",
                    "reason": "integrity_check_failed",
                }
            ],
            errors=[
                {
                    "backup_file": f"{self.backup_dir}/api_20261010_040000_000000.json",
                    "secret_id": "api",
                    "error": f"boom reading {self.backup_dir}/api_20261010_040000_000000.json",
                }
            ],
        )
        self.checker.history = [report]
        latest = self.service.summary()["latest"]
        self.assertEqual(latest["failures_total"], 2)
        first, second = latest["failures"]
        self.assertEqual(first["file"], "db_20261010_040000_000000.json")
        self.assertEqual(first["reason"], "Integrity check failed")
        self.assertEqual(second["file"], "api_20261010_040000_000000.json")
        self.assertNotIn(str(self.backup_dir), second["reason"])
        self.assertNotIn(str(self.backup_dir), repr(latest))

    def test_failures_are_capped_but_counted(self):
        corrupted = [
            {"backup_file": f"/x/f{i}.json", "secret_id": "s", "reason": "x"} for i in range(80)
        ]
        self.checker.history = [run_report(total=100, failed=80, corrupted=corrupted)]
        latest = self.service.summary()["latest"]
        self.assertEqual(len(latest["failures"]), MAX_FAILURES_LISTED)
        self.assertEqual(latest["failures_total"], 80)

    def test_falls_back_to_a_fresh_checker_without_a_scheduler(self):
        engine = SimpleNamespace(
            scheduler=None, backup_manager=MagicMock(backup_dir=self.backup_dir)
        )
        service = BackupHealthService(engine, audit=self.audit)
        with patch(
            "secret_rotator.backup_health.BackupIntegrityChecker", wraps=BackupIntegrityChecker
        ) as cls:
            cls.classify_success_rate = BackupIntegrityChecker.classify_success_rate
            cls.return_value = self.checker
            self.assertEqual(service.summary()["status"], "unknown")
            cls.assert_called_once()


class TestManualVerification(ServiceFixture):
    def test_runs_in_the_background_and_completes(self):
        self.checker.verify_result = run_report(total=10, failed=0)
        run, started = self.service.start_verification("alice")
        self.assertTrue(started)
        self.assertEqual(run["actor"], "alice")
        self.finish()
        state = self.service.verification_status()
        self.assertEqual(state["status"], RUN_COMPLETED)
        self.assertEqual(state["report"], {"total": 10, "verified": 10, "failed": 0})
        self.assertIsNotNone(state["finished_at"])
        self.assertIsNone(state["error"])

    def test_start_returns_before_verification_finishes(self):
        self.checker.verify_gate = threading.Event()
        run, started = self.service.start_verification("alice")
        self.assertTrue(started)
        self.assertEqual(run["status"], "running")
        self.assertTrue(self.service.summary()["running"])
        self.checker.verify_gate.set()
        self.finish()
        self.assertFalse(self.service.summary()["running"])

    def test_only_one_verification_at_a_time(self):
        self.checker.verify_gate = threading.Event()
        self.service.start_verification("alice")
        run, started = self.service.start_verification("bob")
        self.assertFalse(started)
        self.assertEqual(run["actor"], "alice")
        self.checker.verify_gate.set()
        self.finish()
        self.assertEqual(self.checker.verify_calls, 1)

    def test_can_run_again_after_finishing(self):
        self.service.start_verification("alice")
        self.finish()
        _, started = self.service.start_verification("alice")
        self.assertTrue(started)
        self.finish()
        self.assertEqual(self.checker.verify_calls, 2)

    def test_audit_event_records_actor_and_counts(self):
        self.checker.verify_result = run_report(total=10, failed=2)
        self.service.start_verification("alice")
        self.finish()
        event = self.audit.get_recent_events(action="verify_backups")[0]
        self.assertEqual(event["actor"], "alice")
        self.assertFalse(event["success"])
        self.assertEqual(event["details"], {"total": 10, "verified": 8, "failed": 2})

    def test_clean_run_is_a_successful_audit_event(self):
        self.service.start_verification("alice")
        self.finish()
        self.assertTrue(self.audit.get_recent_events(action="verify_backups")[0]["success"])

    def test_exception_marks_the_run_failed_without_leaking_it(self):
        self.checker.verify_raises = RuntimeError("secret path /srv/data exploded")
        self.service.start_verification("alice")
        self.finish()
        state = self.service.verification_status()
        self.assertEqual(state["status"], RUN_FAILED)
        self.assertNotIn("/srv/data", state["error"])
        event = self.audit.get_recent_events(action="verify_backups")[0]
        self.assertFalse(event["success"])
        self.assertNotIn("/srv/data", str(event))

    def test_process_level_error_marks_the_run_failed(self):
        self.checker.verify_result = {
            **run_report(total=0),
            "errors": [{"error": "disk gone", "context": "verification_process"}],
        }
        self.service.start_verification("alice")
        self.finish()
        self.assertEqual(self.service.verification_status()["status"], RUN_FAILED)

    def test_can_start_again_after_a_failure(self):
        self.checker.verify_raises = RuntimeError("x")
        self.service.start_verification("alice")
        self.finish()
        self.checker.verify_raises = None
        _, started = self.service.start_verification("alice")
        self.assertTrue(started)
        self.finish()

    def test_status_is_none_before_any_run(self):
        self.assertIsNone(self.service.verification_status())

    def test_status_is_a_copy(self):
        self.service.start_verification("alice")
        self.finish()
        state = self.service.verification_status()
        state["status"] = "tampered"
        state["report"]["failed"] = 99
        again = self.service.verification_status()
        self.assertEqual(again["status"], RUN_COMPLETED)
        self.assertEqual(again["report"]["failed"], 0)


USERNAME = "health-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)


class TestVerificationApi(ServiceFixture):
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
        DistributedLock.reset_local_locks()
        app = create_app(RotationEngine())
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.backup_health = self.service
        self.client = app.test_client()
        limiter.reset()

    def login(self):
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def test_endpoints_require_login(self):
        self.assertEqual(self.client.get("/api/verification/summary").status_code, 401)
        self.assertEqual(self.client.get("/api/verification/status").status_code, 401)
        self.assertEqual(self.client.post("/api/verification/run").status_code, 401)
        self.assertEqual(self.checker.verify_calls, 0)

    def test_summary(self):
        self.login()
        self.checker.history = [run_report(total=44, failed=3)]
        data = self.client.get("/api/verification/summary?days=30").get_json()
        self.assertEqual(data["days"], 30)
        self.assertEqual(data["latest"]["failed"], 3)
        self.assertEqual(data["status"], "critical")

    def test_summary_rejects_out_of_range_days(self):
        self.login()
        for value in ("0", "366", "-1"):
            resp = self.client.get(f"/api/verification/summary?days={value}")
            self.assertEqual(resp.status_code, 400, value)

    def test_non_numeric_days_fall_back_to_the_default(self):
        self.login()
        data = self.client.get("/api/verification/summary?days=abc").get_json()
        self.assertEqual(data["days"], 7)

    def test_run_returns_202_then_409_while_running(self):
        self.login()
        self.checker.verify_gate = threading.Event()
        first = self.client.post("/api/verification/run")
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.get_json()["run"]["actor"], USERNAME)
        second = self.client.post("/api/verification/run")
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["run"]["status"], "running")
        self.checker.verify_gate.set()
        self.finish()

    def test_status_reports_the_run(self):
        self.login()
        self.assertIsNone(self.client.get("/api/verification/status").get_json()["run"])
        self.client.post("/api/verification/run")
        self.finish()
        run = self.client.get("/api/verification/status").get_json()["run"]
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["report"]["failed"], 0)

    def test_get_is_not_allowed_on_run(self):
        self.login()
        self.assertEqual(self.client.get("/api/verification/run").status_code, 405)

    def test_run_is_rate_limited(self):
        self.login()
        codes = []
        for _ in range(6):
            codes.append(self.client.post("/api/verification/run").status_code)
            self.finish()
        self.assertEqual(codes[-1], 429)
        self.assertNotIn(429, codes[:-1])

    def test_responses_never_contain_server_paths(self):
        self.login()
        self.checker.history = [
            run_report(
                failed=1,
                corrupted=[
                    {
                        "backup_file": f"{self.backup_dir}/db_20261010_040000_000000.json",
                        "secret_id": "db",
                    }
                ],
            )
        ]
        body = self.client.get("/api/verification/summary").get_data(as_text=True)
        self.assertNotIn(str(self.backup_dir), body)

    def test_the_old_verification_endpoints_are_gone(self):
        self.login()
        for method, url in (
            ("get", "/api/backup-health"),
            ("get", "/api/verification-history"),
            ("post", "/api/run-verification"),
        ):
            self.assertEqual(getattr(self.client, method)(url).status_code, 404, url)
        # ... and the liveness probe the container healthcheck uses is not
        self.assertEqual(self.client.application.test_client().get("/api/healthz").status_code, 200)


if __name__ == "__main__":
    unittest.main()

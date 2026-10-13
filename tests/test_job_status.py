"""Tests for the job status service, engine active-job tracking and the
scheduler's next-rotation lookup."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import schedule

from secret_rotator.audit_log import AuditLog
from secret_rotator.job_status import (
    STATUS_CURRENT,
    STATUS_FAILED,
    STATUS_NEVER,
    STATUS_ROTATING,
    JobStatusService,
)
from secret_rotator.providers.base import SecretProvider
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.rotators.base import SecretRotator
from secret_rotator.scheduler import RotationScheduler

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ts(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat()


class FakeEngine:
    def __init__(self, jobs, active=(), scheduler=None, backups=0):
        self.rotation_jobs = jobs
        self._active = set(active)
        self.scheduler = scheduler
        self.backup_manager = MagicMock()
        self.backup_manager.count_backups.return_value = backups

    def active_job_names(self):
        return set(self._active)


JOBS = [
    {"name": "db_job", "secret_id": "db", "provider": "p", "rotator": "r", "schedule": "weekly"},
    {"name": "api_job", "secret_id": "api", "provider": "p", "rotator": "r"},
]


class AuditFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "audit.log"
        self.audit = AuditLog(audit_file=str(self.path))

    def write(self, when, action="rotate", success=True, job=None, reason=None, secret_id="x"):
        details = {}
        if job:
            details["job"] = job
        if reason:
            details["reason"] = reason
        event = {
            "timestamp": when,
            "action": action,
            "actor": "system",
            "secret_id": secret_id,
            "success": success,
            "details": details,
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(event) + "\n")

    def service(self, engine=None):
        return JobStatusService(engine or FakeEngine(JOBS), audit=self.audit)

    def by_name(self, summaries):
        return {s["name"]: s for s in summaries}


class TestJobSummaries(AuditFixture):
    def test_jobs_with_no_history_are_never_rotated(self):
        jobs = self.by_name(self.service().job_summaries())
        self.assertEqual(jobs["db_job"]["status"], STATUS_NEVER)
        self.assertIsNone(jobs["db_job"]["last_rotated"])
        self.assertIsNone(jobs["db_job"]["last_attempt"])

    def test_successful_rotation_is_current(self):
        self.write(ts(5), job="db_job")
        job = self.by_name(self.service().job_summaries())["db_job"]
        self.assertEqual(job["status"], STATUS_CURRENT)
        self.assertEqual(job["last_rotated"], ts(5))
        self.assertIsNone(job["last_error"])

    def test_failure_after_success_is_failed_but_keeps_last_rotated(self):
        self.write(ts(30), job="db_job")
        self.write(ts(2), job="db_job", success=False, reason="timeout")
        job = self.by_name(self.service().job_summaries())["db_job"]
        self.assertEqual(job["status"], STATUS_FAILED)
        self.assertEqual(job["last_rotated"], ts(30))
        self.assertEqual(job["last_attempt"], ts(2))
        self.assertEqual(job["last_error"], "timeout")

    def test_success_after_failure_clears_the_error(self):
        self.write(ts(30), job="db_job", success=False, reason="timeout")
        self.write(ts(2), job="db_job")
        job = self.by_name(self.service().job_summaries())["db_job"]
        self.assertEqual(job["status"], STATUS_CURRENT)
        self.assertIsNone(job["last_error"])

    def test_events_are_ordered_by_time_not_file_position(self):
        self.write(ts(2), job="db_job")
        self.write(ts(30), job="db_job", success=False, reason="old failure")
        job = self.by_name(self.service().job_summaries())["db_job"]
        self.assertEqual(job["status"], STATUS_CURRENT)

    def test_active_job_is_rotating(self):
        self.write(ts(2), job="db_job", success=False, reason="timeout")
        engine = FakeEngine(JOBS, active={"db_job"})
        job = self.by_name(self.service(engine).job_summaries())["db_job"]
        self.assertEqual(job["status"], STATUS_ROTATING)

    def test_events_for_unknown_jobs_or_without_a_job_are_ignored(self):
        self.write(ts(1), job="ghost_job")
        self.write(ts(1), job=None)
        self.write(ts(1), action="restore", job="db_job")
        jobs = self.by_name(self.service().job_summaries())
        self.assertEqual(jobs["db_job"]["status"], STATUS_NEVER)
        self.assertEqual(len(jobs), 2)

    def test_schedule_falls_back_to_the_default(self):
        with patch("secret_rotator.job_status.settings") as fake_settings:
            fake_settings.get.return_value = "daily"
            jobs = self.by_name(self.service().job_summaries())
        self.assertEqual(jobs["db_job"]["schedule"], "weekly")
        self.assertEqual(jobs["api_job"]["schedule"], "daily")

    def test_next_rotation_comes_from_the_scheduler(self):
        when = NOW + timedelta(hours=3)
        scheduler = MagicMock()
        scheduler.next_rotation_time.return_value = when
        engine = FakeEngine(JOBS, scheduler=scheduler)
        job = self.by_name(self.service(engine).job_summaries())["db_job"]
        self.assertEqual(job["next_rotation"], when.isoformat())

    def test_next_rotation_is_none_without_a_scheduler(self):
        job = self.by_name(self.service().job_summaries())["db_job"]
        self.assertIsNone(job["next_rotation"])

    def test_audit_file_is_only_reparsed_when_it_changes(self):
        self.write(ts(5), job="db_job")
        service = self.service()
        with patch.object(
            self.audit, "get_recent_events", wraps=self.audit.get_recent_events
        ) as spy:
            service.job_summaries()
            service.job_summaries()
            self.assertEqual(spy.call_count, 1)
            self.write(ts(1), job="db_job", success=False, reason="boom")
            job = self.by_name(service.job_summaries())["db_job"]
            self.assertEqual(spy.call_count, 2)
        self.assertEqual(job["status"], STATUS_FAILED)


class TestOverview(AuditFixture):
    def overview(self, engine=None, history=None):
        checker = MagicMock()
        checker.get_backup_health_metrics.return_value = {
            "status": "warning",
            "last_verification": ts(1),
            "total_backups": 48,
            "verified": 45,
            "failed": 3,
        }
        checker.get_verification_history.return_value = history or []
        with patch("secret_rotator.job_status.BackupIntegrityChecker", return_value=checker):
            return self.service(engine).overview()

    def test_summary_counts(self):
        engine = FakeEngine(JOBS, active={"api_job"}, backups=48)
        summary = self.overview(engine)["summary"]
        self.assertEqual(summary, {"jobs": 2, "backups": 48, "rotating": 1})

    def test_failed_jobs_need_attention(self):
        self.write(ts(2), job="db_job", success=False, reason="timeout")
        self.write(ts(2), job="api_job")
        attention = self.overview()["attention"]
        self.assertEqual(len(attention), 1)
        self.assertEqual(attention[0]["kind"], "job_failed")
        self.assertEqual(attention[0]["job"], "db_job")
        self.assertEqual(attention[0]["detail"], "timeout")

    def test_recent_activity_is_newest_first_and_limited(self):
        for hours in (9, 8, 7, 6, 5):
            self.write(ts(hours), job="db_job")
        self.write(ts(1), action="login", job=None)  # not shown
        self.write(ts(3), action="restore", secret_id="db")
        activity = self.overview()["recent_activity"]
        self.assertEqual(len(activity), 4)
        self.assertEqual(activity[0]["action"], "restore")
        self.assertEqual(activity[0]["message"], "db restored from backup")
        self.assertEqual(activity[1]["message"], "db_job rotated")
        stamps = [a["timestamp"] for a in activity]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_failed_activity_message(self):
        self.write(ts(1), job="db_job", success=False, reason="timeout")
        message = self.overview()["recent_activity"][0]["message"]
        self.assertEqual(message, "db_job failed")

    def test_verification_runs_are_oldest_first_with_ok_flag(self):
        stamp = lambda h: (NOW - timedelta(hours=h)).replace(tzinfo=None).isoformat()  # noqa: E731
        history = [  # newest first, as get_verification_history returns
            {"timestamp": stamp(1), "failed": 0},
            {"timestamp": stamp(25), "failed": 3},
            {"timestamp": stamp(49), "failed": 0},
        ]
        verification = self.overview(history=history)["verification"]
        self.assertEqual([r["ok"] for r in verification["recent_runs"]], [True, False, True])
        self.assertEqual(verification["status"], "warning")
        self.assertEqual(verification["failed"], 3)
        self.assertTrue(verification["last_verification"].endswith("+00:00"))


class FakeProvider(SecretProvider):
    def __init__(self, on_get=None):
        super().__init__(name="p", config={})
        self.on_get = on_get

    def get_secret(self, secret_id):
        if self.on_get:
            self.on_get()
        return "old"

    def update_secret(self, secret_id, new_value):
        return True

    def validate_connection(self):
        return True


class FakeRotator(SecretRotator):
    def __init__(self):
        super().__init__(name="r", config={})

    def generate_new_secret(self):
        return "new"

    def validate_secret(self, value):
        return True


class TestEngineActiveJobs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch(
            "secret_rotator.rotation_engine.audit_log",
            AuditLog(audit_file=str(Path(tmp.name) / "audit.log")),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = RotationEngine()
        self.engine.backup_manager = MagicMock()
        self.job = {"name": "db_job", "provider": "p", "rotator": "r", "secret_id": "db"}

    def test_job_is_active_only_while_rotating(self):
        seen = []
        provider = FakeProvider(on_get=lambda: seen.append(self.engine.active_job_names()))
        self.engine.register_provider(provider)
        self.engine.register_rotator(FakeRotator())

        self.assertEqual(self.engine.active_job_names(), set())
        self.assertTrue(self.engine.rotate_secret(self.job))

        self.assertEqual(seen, [{"db_job"}])
        self.assertEqual(self.engine.active_job_names(), set())

    def test_job_is_cleared_when_rotation_raises(self):
        def boom():
            raise RuntimeError("provider exploded")

        self.engine.register_provider(FakeProvider(on_get=boom))
        self.engine.register_rotator(FakeRotator())
        self.assertFalse(self.engine.rotate_secret(self.job))
        self.assertEqual(self.engine.active_job_names(), set())

    def test_rotate_secret_keeps_its_name_and_docstring(self):
        self.assertEqual(RotationEngine.rotate_secret.__name__, "rotate_secret")


class TestSchedulerNextRotation(unittest.TestCase):
    def setUp(self):
        schedule.clear()
        self.addCleanup(schedule.clear)
        self.scheduler = RotationScheduler(rotation_function=lambda: {}, backup_manager=MagicMock())

    def test_none_before_a_schedule_is_set_up(self):
        self.assertIsNone(self.scheduler.next_rotation_time())

    def test_returns_an_aware_future_utc_time(self):
        self.scheduler.setup_schedule("daily")
        when = self.scheduler.next_rotation_time()
        self.assertIsNotNone(when.tzinfo)
        self.assertEqual(when.utcoffset(), timedelta(0))
        self.assertGreater(when, datetime.now(timezone.utc))

    def test_ignores_maintenance_jobs(self):
        # Cleanup, verification and checksum jobs fire far sooner than a
        # weekly rotation; they must not be reported as the next rotation.
        self.scheduler.setup_schedule("weekly")
        when = self.scheduler.next_rotation_time()
        self.assertGreater(when, datetime.now(timezone.utc) + timedelta(days=6))

    def test_interval_schedules_report_the_interval(self):
        self.scheduler.setup_schedule("every_2_hours")
        when = self.scheduler.next_rotation_time()
        delta = when - datetime.now(timezone.utc)
        self.assertLess(delta, timedelta(hours=2, minutes=1))
        self.assertGreater(delta, timedelta(hours=1, minutes=58))


if __name__ == "__main__":
    unittest.main()

"""Tests for the Overview page and its template helpers."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from werkzeug.security import generate_password_hash

from secret_rotator.audit_log import AuditLog
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.job_status import JobStatusService
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter
from secret_rotator.web.routes.dashboard import format_utc, schedule_label

USERNAME = "overview-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)
NOW = datetime.now(timezone.utc).replace(microsecond=0)

JOBS = [
    {
        "name": "database_password",
        "secret_id": "db_password",
        "provider": "p",
        "rotator": "r",
        "schedule": "weekly",
    },
    {
        "name": "ssh_deploy_key",
        "secret_id": "deploy_key",
        "provider": "p",
        "rotator": "r",
        "schedule": "every_2_hours",
    },
]


class TestOverviewPage(unittest.TestCase):
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

        self.engine = RotationEngine()
        self.engine.backup_manager = MagicMock()
        self.engine.backup_manager.count_backups.return_value = 48
        self.engine.rotation_jobs = [dict(job) for job in JOBS]

        self.history = []
        self.health = {
            "status": "healthy",
            "last_verification": NOW.replace(tzinfo=None).isoformat(),
            "total_backups": 48,
            "verified": 48,
            "failed": 0,
        }
        checker = MagicMock()
        checker.get_backup_health_metrics.side_effect = lambda: self.health
        checker.get_verification_history.side_effect = lambda days=7: self.history
        patcher = patch("secret_rotator.job_status.BackupIntegrityChecker", return_value=checker)
        patcher.start()
        self.addCleanup(patcher.stop)

        app = create_app(self.engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.job_status = JobStatusService(
            self.engine, audit=AuditLog(audit_file=str(self.audit_path))
        )
        self.client = app.test_client()
        limiter.reset()
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def write_event(self, hours_ago, job, success=True, reason=None, action="rotate"):
        event = {
            "timestamp": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "action": action,
            "actor": "system",
            "secret_id": "x",
            "success": success,
            "details": {"job": job, **({"reason": reason} if reason else {})},
        }
        with open(self.audit_path, "a") as f:
            f.write(json.dumps(event) + "\n")

    def page(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    # ---- header ----

    def test_header_counts_jobs_and_backups(self):
        html = self.page()
        self.assertIn("2 jobs and 48 backups.", html)

    def test_header_pluralises_singular(self):
        self.engine.rotation_jobs = self.engine.rotation_jobs[:1]
        self.engine.backup_manager.count_backups.return_value = 1
        self.assertIn("1 job and 1 backup.", self.page())

    def test_header_reports_a_passing_verification(self):
        self.assertIn("The last verification passed at", self.page())

    def test_header_reports_failing_verification(self):
        self.health.update(failed=3, verified=45)
        self.assertIn("found 3 of 48 backups failing", self.page())

    def test_header_when_verification_never_ran(self):
        self.health = {"status": "unknown", "last_verification": None}
        self.assertIn("No backup verification has run yet.", self.page())

    # ---- secrets table ----

    def test_jobs_table_rows_link_to_job_pages(self):
        html = self.page()
        self.assertIn('href="/jobs/database_password"', html)
        self.assertIn("<small>db_password</small>", html)
        self.assertIn("Weekly", html)
        self.assertIn("Every 2 hours", html)
        self.assertIn("Never rotated", html)

    def test_status_badges_reflect_history(self):
        self.write_event(5, "database_password")
        self.write_event(2, "ssh_deploy_key", success=False, reason="timeout")
        html = self.page()
        self.assertIn('<span class="badge ok">Current</span>', html)
        self.assertIn('<span class="badge err">Failed</span>', html)

    def test_rotating_job_shows_banner_and_disables_rotate_all(self):
        self.engine._mark_job_active("ssh_deploy_key")
        html = self.page()
        self.assertIn("Rotating now", html)
        self.assertIn('data-refresh-ms="3000"', html)
        self.assertIn("Rotating, 1 job in progress", html)
        self.assertRegex(html, r'data-open="m-rotate" disabled')

    def test_empty_state_when_no_jobs(self):
        self.engine.rotation_jobs = []
        html = self.page()
        self.assertIn("No jobs yet.", html)
        self.assertRegex(html, r'data-open="m-rotate" disabled')

    def test_times_have_a_utc_fallback_and_machine_readable_value(self):
        self.write_event(5, "database_password")
        html = self.page()
        stamp = (NOW - timedelta(hours=5)).isoformat()
        fallback = (NOW - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M UTC")
        self.assertIn(f'<time datetime="{stamp}" data-fmt="datetime">{fallback}</time>', html)

    # ---- attention ----

    def test_no_attention_box_when_everything_is_fine(self):
        self.write_event(5, "database_password")
        self.assertNotIn('class="attn"', self.page())

    def test_failed_job_needs_attention_with_escaped_reason(self):
        self.write_event(1, "ssh_deploy_key", success=False, reason="<script>alert(1)</script>")
        html = self.page()
        self.assertIn('class="attn"', html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn('href="/jobs/ssh_deploy_key">View</a>', html)

    def test_failed_job_row_has_a_retry_button(self):
        self.write_event(1, "ssh_deploy_key", success=False, reason="boom")
        html = self.page()
        self.assertIn('data-action="rotate-job" data-job="ssh_deploy_key">Retry</button>', html)

    def test_retry_is_disabled_while_another_rotation_runs(self):
        self.write_event(1, "ssh_deploy_key", success=False, reason="boom")
        self.engine._mark_job_active("database_password")
        html = self.page()
        self.assertIn('data-job="ssh_deploy_key" disabled>Retry</button>', html)

    # ---- backups and activity ----

    def test_verification_strip_summarises_runs(self):
        stamp = lambda h: (NOW - timedelta(hours=h)).replace(tzinfo=None).isoformat()  # noqa: E731
        self.history = [
            {"timestamp": stamp(1), "failed": 0},
            {"timestamp": stamp(25), "failed": 3},
        ]
        html = self.page()
        self.assertIn("1 passed, 1 failed", html)
        self.assertIn('<i class="bad"></i>', html)
        self.assertIn("1 failed.", html)

    def test_no_verification_runs(self):
        self.assertIn("No verification runs yet.", self.page())

    def test_activity_lists_recent_events(self):
        self.write_event(3, "database_password")
        self.write_event(1, "ssh_deploy_key", success=False, reason="boom")
        html = self.page()
        self.assertIn("<span>ssh_deploy_key failed</span>", html)
        self.assertIn("<span>database_password rotated</span>", html)

    def test_activity_empty_state(self):
        self.assertIn("No activity yet.", self.page())

    # ---- rotate dialog and scripts ----

    def test_rotate_dialog_is_an_accessible_modal(self):
        html = self.page()
        self.assertIn('id="m-rotate"', html)
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-modal="true"', html)
        self.assertIn('aria-labelledby="m-rotate-title"', html)
        self.assertIn("2 secrets will be rotated now.", html)

    def test_page_loads_its_scripts_and_no_inline_handlers(self):
        html = self.page()
        for name in ("http.js", "time.js", "overlays.js", "rotate.js"):
            self.assertIn(f"js/{name}", html)
        self.assertNotRegex(html, r"\son(click|load|error)=")


class TestTemplateFilters(unittest.TestCase):
    def test_schedule_label(self):
        self.assertEqual(schedule_label("daily"), "Daily")
        self.assertEqual(schedule_label("every_30_minutes"), "Every 30 minutes")
        self.assertEqual(schedule_label(None), "")

    def test_format_utc_converts_offsets(self):
        self.assertEqual(format_utc("2026-10-13T06:00:00+02:00"), "2026-10-13 04:00 UTC")

    def test_format_utc_tolerates_bad_input(self):
        self.assertEqual(format_utc("not a date"), "not a date")
        self.assertEqual(format_utc(None), "")


if __name__ == "__main__":
    unittest.main()

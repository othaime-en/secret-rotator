"""Tests for the job page (/jobs/<name>) and the duration_label filter."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.security import generate_password_hash

from secret_rotator.audit_log import AuditLog
from secret_rotator.backup_manager import BackupManager
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.job_status import JobStatusService
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.rotators.advanced_rotators import DatabasePasswordRotator
from secret_rotator.rotators.password_rotator import PasswordRotator
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter
from secret_rotator.web.routes.dashboard import duration_label

USERNAME = "job-page-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ts(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat()


class TestJobPage(unittest.TestCase):
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
        self.backup_dir = Path(tmp.name) / "backups"
        self.backup_dir.mkdir()

        self.engine = RotationEngine()
        self.engine.backup_manager = BackupManager(
            backup_dir=str(self.backup_dir), encrypt_backups=False
        )
        self.engine.rotators["password_gen"] = PasswordRotator(
            "password_gen", {"length": 20, "exclude_ambiguous": True}
        )
        self.engine.rotators["db_rot"] = DatabasePasswordRotator(
            "db_rot",
            {"host": "db.internal.example.com", "username": "svc_rotator", "database": "payments"},
        )
        self.engine.rotation_jobs = [
            {
                "name": "db_job",
                "secret_id": "db_password",
                "provider": "file_storage",
                "rotator": "password_gen",
                "schedule": "weekly",
                "notification": True,
            },
            {"name": "pg_job", "secret_id": "pg", "provider": "p", "rotator": "db_rot"},
            {"name": "orphan", "secret_id": "o", "provider": "p", "rotator": "missing"},
        ]

        app = create_app(self.engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.job_status = JobStatusService(
            self.engine, audit=AuditLog(audit_file=str(self.audit_path))
        )
        self.client = app.test_client()
        limiter.reset()
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

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

    def backup(self, secret_id, day, encrypted=True):
        (self.backup_dir / f"{secret_id}_202610{day:02d}_040000_000000.json").write_text(
            json.dumps(
                {
                    "secret_id": secret_id,
                    "encrypted": encrypted,
                    "backup_created": f"2026-10-{day:02d}T04:00:00",
                    "old_value": "OLD-SECRET",
                    "new_value": "NEW-SECRET",
                }
            )
        )

    def page(self, name="db_job"):
        resp = self.client.get(f"/jobs/{name}")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    # ---- header ----

    def test_header_breadcrumb_and_status(self):
        self.event(5)
        html = self.page()
        self.assertIn('<a class="lnk" href="/jobs">Jobs</a> / db_job', html)
        self.assertIn("<h1>db_job</h1>", html)
        self.assertIn('<span class="badge ok">Current</span>', html)

    def test_rotate_now_button(self):
        html = self.page()
        self.assertRegex(html, r'data-action="rotate-job" data-job="db_job">Rotate now</button>')

    def test_rotate_now_is_disabled_while_this_job_rotates(self):
        self.engine._mark_job_active("db_job")
        html = self.page()
        self.assertRegex(html, r'data-job="db_job" disabled>Rotate now')
        self.assertIn("Rotating, 1 job in progress", html)
        self.assertIn('data-refresh-ms="3000"', html)

    def test_failed_job_shows_the_reason_escaped(self):
        self.event(3, success=False, reason="<script>alert(1)</script>")
        html = self.page()
        self.assertIn("The last attempt failed", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_no_failure_note_for_a_healthy_job(self):
        self.event(3)
        self.assertNotIn("The last attempt failed", self.page())

    def test_edit_pause_and_delete_are_not_offered_yet(self):
        html = self.page()
        for word in (">Edit<", ">Pause<", "Delete job"):
            self.assertNotIn(word, html)

    # ---- summary band ----

    def test_band_for_a_new_job(self):
        html = self.page()
        self.assertIn("<dt>Last rotated</dt><dd>Never</dd>", html)
        self.assertIn('<dt>Success rate</dt><dd><span class="muted">—</span></dd>', html)
        self.assertIn("<dt>Backups</dt><dd>0</dd>", html)

    def test_band_with_history(self):
        self.event(50)
        self.event(40, success=False, reason="x")
        self.event(30)
        self.event(20)
        self.backup("db_password", 1)
        self.backup("db_password", 2)
        html = self.page()
        self.assertIn('title="3 of 4 runs succeeded">75%', html)
        self.assertIn("<dt>Backups</dt><dd>2</dd>", html)
        self.assertIn(f'<time datetime="{ts(20)}"', html)

    # ---- run history ----

    def test_run_history_rows(self):
        self.event(30, duration_ms=1234)
        self.event(20, actor="admin", duration_ms=450)
        self.event(10, success=False, reason="timeout", duration_ms=30000)
        html = self.page()
        self.assertIn('<td class="mono">1.2s</td>', html)
        self.assertIn('<td class="mono">450ms</td>', html)
        self.assertIn('<td class="mono">30.0s</td>', html)
        self.assertIn("<td>Schedule</td>", html)
        self.assertIn("<td>Manual, admin</td>", html)
        self.assertIn(
            '<span class="badge err">Failed</span><div class="reason muted">timeout</div>', html
        )
        self.assertEqual(html.count('<span class="badge ok">Success</span>'), 2)

    def test_runs_are_newest_first(self):
        self.event(30, actor="first")
        self.event(10, actor="third")
        html = self.page()
        self.assertLess(html.index("Manual, third"), html.index("Manual, first"))

    def test_old_runs_without_a_duration(self):
        self.event(5)
        self.assertIn('<td class="mono">—</td>', self.page())

    def test_no_runs_message(self):
        self.assertIn("No runs recorded yet.", self.page())

    def test_notes_when_history_is_truncated(self):
        for i in range(25):
            self.event(100 - i)
        self.assertIn("Showing the latest 20 of 25 runs.", self.page())

    # ---- backups ----

    def test_backups_of_this_secret_only_with_restore_buttons(self):
        self.backup("db_password", 3)
        self.backup("db_password", 2, encrypted=False)
        self.backup("old_db_password", 4)
        html = self.page()
        self.assertIn("db_password_20261003_040000_000000.json", html)
        self.assertNotIn("old_db_password_", html)
        self.assertIn('<span class="badge warn">Unencrypted</span>', html)
        self.assertRegex(
            html,
            r'data-open="m-restore"\s+data-file="db_password_20261003_040000_000000.json" data-secret="db_password"',
        )
        self.assertIn('href="/backups?q=db_password">All backups</a>', html)

    def test_no_backups_message(self):
        self.assertIn("No backups yet.", self.page())

    def test_restore_dialog_and_scripts_are_present(self):
        html = self.page()
        self.assertIn('id="m-restore"', html)
        self.assertIn('data-action="confirm-restore"', html)
        for name in ("rotate.js", "backups.js", "flash.js"):
            self.assertIn(f"js/{name}", html)
        self.assertNotRegex(html, r"\son(click|load|error|input)=")

    # ---- configuration ----

    def test_configuration(self):
        html = self.page()
        self.assertIn('<dt>Secret ID</dt><dd><span class="mono">db_password</span></dd>', html)
        self.assertIn("<dt>Provider</dt><dd>file_storage</dd>", html)
        self.assertIn("<dt>Rotator</dt><dd>password_gen</dd>", html)
        self.assertIn("<dt>Schedule</dt><dd>Weekly</dd>", html)
        self.assertIn("<dt>Notifications</dt><dd>On</dd>", html)

    def test_rotator_settings(self):
        html = self.page()
        self.assertIn("<dt>Length</dt><dd>20</dd>", html)
        self.assertIn("<dt>Characters</dt><dd>a-z A-Z 0-9 symbols</dd>", html)
        self.assertIn("<dt>Skip look-alikes</dt><dd>Yes</dd>", html)
        self.assertIn('href="/rotators">Open</a>', html)

    def test_connection_details_are_never_rendered(self):
        html = self.page("pg_job")
        self.assertIn("<dt>Database type</dt>", html)
        for hidden in ("db.internal", "svc_rotator", "payments"):
            self.assertNotIn(hidden, html)

    def test_missing_rotator_is_explained(self):
        html = self.page("orphan")
        self.assertIn("is not configured, so this job cannot rotate", html)

    def test_no_secret_values_or_server_paths(self):
        self.backup("db_password", 1)
        html = self.page()
        self.assertNotIn("SECRET", html.replace("secret", ""))
        self.assertNotIn(str(self.backup_dir), html)

    # ---- routing ----

    def test_unknown_job_is_a_404_page(self):
        resp = self.client.get("/jobs/nope")
        self.assertEqual(resp.status_code, 404)
        html = resp.get_data(as_text=True)
        self.assertIn("<h1>Job not found</h1>", html)
        self.assertIn("There is no job named “nope”.", html)
        self.assertIn('href="/jobs">Back to jobs</a>', html)

    def test_job_names_with_slashes(self):
        self.engine.rotation_jobs.append(
            {"name": "prod/db", "secret_id": "pdb", "provider": "p", "rotator": "password_gen"}
        )
        self.assertIn("<h1>prod/db</h1>", self.page("prod/db"))

    def test_jobs_nav_item_is_highlighted(self):
        self.assertRegex(self.page(), r'<a href="/jobs" class="on" aria-current="page">Jobs</a>')

    def test_requires_login(self):
        anonymous = self.client.application.test_client()
        self.assertEqual(anonymous.get("/jobs/db_job").status_code, 302)


class TestDurationLabel(unittest.TestCase):
    def test_labels(self):
        cases = {
            None: "—",
            0: "0ms",
            450: "450ms",
            999: "999ms",
            1000: "1.0s",
            1234: "1.2s",
            30000: "30.0s",
            65000: "1m 05s",
            3600000: "60m 00s",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(duration_label(value), expected)


if __name__ == "__main__":
    unittest.main()

"""Tests for the Backup health page."""

import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from werkzeug.security import generate_password_hash

from secret_rotator.audit_log import AuditLog
from secret_rotator.backup_health import BackupHealthService
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

USERNAME = "health-page-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)
NOW = datetime.now()


def report(total=48, failed=0, days_ago=0, corrupted=None, errors=None):
    return {
        "timestamp": (NOW - timedelta(days=days_ago, minutes=1)).isoformat(),
        "total_backups": total,
        "verified": total - failed,
        "failed": failed,
        "corrupted": corrupted or [],
        "errors": errors or [],
    }


class FakeChecker:
    def __init__(self):
        self.history = []

    def get_verification_history(self, days=30):
        cutoff = NOW - timedelta(days=days)
        return [r for r in self.history if datetime.fromisoformat(r["timestamp"]) > cutoff]

    def verify_all_backups(self):
        return report()


class TestBackupHealthPage(unittest.TestCase):
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
        self.backup_dir = Path(tmp.name) / "backups"
        self.backup_dir.mkdir()

        self.checker = FakeChecker()
        engine = RotationEngine()
        engine.scheduler = SimpleNamespace(integrity_checker=self.checker)
        engine.backup_manager = SimpleNamespace(backup_dir=self.backup_dir)

        app = create_app(engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.service = BackupHealthService(
            engine, audit=AuditLog(audit_file=str(Path(tmp.name) / "audit.log"))
        )
        app.backup_health = self.service
        self.client = app.test_client()
        limiter.reset()
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def page(self, query=""):
        resp = self.client.get("/backup-health" + query)
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    # ---- headline ----

    def test_no_verification_yet(self):
        html = self.page()
        self.assertIn("No verification has run yet.", html)
        self.assertNotIn('class="facts"', html)
        self.assertNotIn('class="strip"', html)
        self.assertIn("No verification runs in the last 7 days.", html)

    def test_all_passed(self):
        self.checker.history = [report(total=48)]
        html = self.page()
        self.assertIn("All 48 backups passed verification.", html)
        self.assertIn("The last run finished", html)
        self.assertIn("<div><b>48</b><span>Backups</span></div>", html)
        self.assertIn("<div><b>48</b><span>Verified</span></div>", html)
        self.assertIn("<div><b>0</b><span>Failed</span></div>", html)

    def test_failures_headline_and_red_failed_count(self):
        self.checker.history = [report(total=44, failed=3)]
        html = self.page()
        self.assertIn("3 of 44 backups failed verification.", html)
        self.assertIn('<b class="bad">3</b><span>Failed</span>', html)

    def test_earlier_failures_are_mentioned_when_latest_run_is_clean(self):
        self.checker.history = [
            report(days_ago=0),
            report(days_ago=1, failed=3),
            report(days_ago=2),
        ]
        self.assertIn("1 of the last 3 runs found failures.", self.page())

    def test_no_backups_to_verify(self):
        self.checker.history = [report(total=0)]
        self.assertIn("There are no backups to verify yet.", self.page())

    # ---- strip ----

    def test_strip_marks_failed_runs_and_describes_itself(self):
        self.checker.history = [report(days_ago=d, failed=3 if d == 3 else 0) for d in range(7)]
        html = self.page()
        self.assertEqual(html.count('<i class="bad"></i>'), 1)
        self.assertIn("6 passed, 1 failed", html)
        self.assertIn("Last 7 runs, oldest first", html)

    # ---- history table ----

    def test_history_rows_and_rate_badges(self):
        self.checker.history = [
            report(total=48, days_ago=0),
            report(total=100, failed=3, days_ago=1),
            report(total=44, failed=3, days_ago=2),
        ]
        html = self.page()
        self.assertIn('<span class="badge ok">100%</span>', html)
        self.assertIn('<span class="badge warn">97%</span>', html)
        self.assertIn('<span class="badge err">93.2%</span>', html)

    def test_range_switch_uses_real_links_and_marks_the_current_one(self):
        html = self.page()
        self.assertIn('href="/backup-health" class="on" aria-current="true">7d</a>', html)
        self.assertIn('href="/backup-health?days=30">30d</a>', html)
        self.assertIn('href="/backup-health?days=90">90d</a>', html)
        self.assertIn('aria-label="History range"', html)

    def test_range_controls_how_far_back_the_table_goes(self):
        self.checker.history = [report(days_ago=1), report(days_ago=20), report(days_ago=60)]
        self.assertEqual(len(re.findall(r"<tr>\s*<td><time", self.page("?days=7"))), 1)
        self.assertEqual(len(re.findall(r"<tr>\s*<td><time", self.page("?days=30"))), 2)
        self.assertEqual(len(re.findall(r"<tr>\s*<td><time", self.page("?days=90"))), 3)

    def test_invalid_range_falls_back_to_seven_days(self):
        for value in ("5", "abc", "-1", "9999"):
            html = self.page(f"?days={value}")
            self.assertIn('class="on" aria-current="true">7d</a>', html, value)

    # ---- failures ----

    def test_failed_backups_are_listed_by_name_with_escaped_reasons(self):
        self.checker.history = [
            report(
                total=10,
                failed=2,
                corrupted=[
                    {
                        "backup_file": f"{self.backup_dir}/db_20261010_040000_000000.json",
                        "secret_id": "db",
                    }
                ],
                errors=[
                    {
                        "backup_file": f"{self.backup_dir}/x_20261010_040000_000000.json",
                        "secret_id": "<b>x</b>",
                        "error": "<script>alert(1)</script>",
                    }
                ],
            )
        ]
        html = self.page()
        self.assertIn("Failed in the latest run", html)
        self.assertIn('<td class="mono">db_20261010_040000_000000.json</td>', html)
        self.assertIn("Integrity check failed", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn(str(self.backup_dir), html)

    def test_no_failures_card_when_everything_passed(self):
        self.checker.history = [report()]
        self.assertNotIn("Failed in the latest run", self.page())

    def test_failure_list_notes_when_it_is_truncated(self):
        corrupted = [{"backup_file": f"/x/f{i}.json", "secret_id": "s"} for i in range(80)]
        self.checker.history = [report(total=100, failed=80, corrupted=corrupted)]
        self.assertIn("Showing 50 of 80", self.page())

    # ---- run button ----

    def test_run_button_is_available_and_accessible(self):
        html = self.page()
        self.assertRegex(
            html, r'<button type="button" class="btn primary" data-action="run-verification">'
        )
        self.assertIn("Run verification now", html)
        self.assertIn('data-running="false"', html)

    def test_run_button_is_disabled_while_a_verification_runs(self):
        import threading

        gate = threading.Event()
        self.checker.verify_all_backups = lambda: (gate.wait(5), report())[1]
        self.service.start_verification("alice")
        try:
            html = self.page()
            self.assertRegex(html, r'data-action="run-verification" disabled')
            self.assertIn('data-running="true"', html)
        finally:
            gate.set()
            self.service._thread.join(5)

    def test_scripts_notice_and_no_inline_handlers(self):
        html = self.page()
        self.assertIn('id="notice"', html)
        for name in ("http.js", "flash.js", "health.js"):
            self.assertIn(f"js/{name}", html)
        self.assertNotRegex(html, r"\son(click|load|error|input)=")

    def test_requires_login(self):
        anonymous = self.client.application.test_client()
        self.assertEqual(anonymous.get("/backup-health").status_code, 302)


if __name__ == "__main__":
    unittest.main()

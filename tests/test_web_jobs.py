"""Tests for the Jobs list page."""

import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from werkzeug.security import generate_password_hash

from secret_rotator.audit_log import AuditLog
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.job_status import JobStatusService
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

USERNAME = "jobs-admin"
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
        "provider": "file_storage",
        "rotator": "password_gen",
        "schedule": "weekly",
    },
    {
        "name": "api_key_stripe",
        "secret_id": "stripe_key",
        "provider": "aws_secrets",
        "rotator": "api_key_gen",
    },
]


class TestJobsPage(unittest.TestCase):
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
        self.engine.rotation_jobs = [dict(job) for job in JOBS]

        app = create_app(self.engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.job_status = JobStatusService(
            self.engine, audit=AuditLog(audit_file=str(self.audit_path))
        )
        self.client = app.test_client()
        limiter.reset()
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def write_event(self, hours_ago, job, success=True, reason=None):
        event = {
            "timestamp": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "action": "rotate",
            "actor": "system",
            "secret_id": "x",
            "success": success,
            "details": {"job": job, **({"reason": reason} if reason else {})},
        }
        with open(self.audit_path, "a") as f:
            f.write(json.dumps(event) + "\n")

    def page(self):
        resp = self.client.get("/jobs")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_lists_every_job_with_details(self):
        html = self.page()
        self.assertIn('href="/jobs/database_password"', html)
        self.assertIn('href="/jobs/api_key_stripe"', html)
        self.assertIn("<small>db_password</small>", html)
        self.assertIn("Weekly", html)
        self.assertIn("Never rotated", html)

    def test_search_box_filters_on_name_secret_id_provider_and_rotator(self):
        html = self.page()
        self.assertIn('type="search"', html)
        self.assertIn('aria-label="Search jobs"', html)
        self.assertIn('data-filter-rows="#job-rows"', html)
        self.assertIn('data-search="database_password db_password file_storage password_gen"', html)
        self.assertIn('id="job-no-match" hidden', html)

    def test_each_row_has_an_accessible_rotate_button(self):
        html = self.page()
        self.assertRegex(
            html,
            r'data-action="rotate-job" data-job="database_password"\s+aria-label="Rotate database_password"',
        )

    def test_failed_job_offers_retry(self):
        self.write_event(1, "api_key_stripe", success=False, reason="timeout")
        html = self.page()
        self.assertIn('aria-label="Retry api_key_stripe"', html)
        self.assertIn('<span class="badge err">Failed</span>', html)

    def test_every_rotate_button_is_disabled_while_a_rotation_runs(self):
        self.engine._mark_job_active("api_key_stripe")
        html = self.page()
        # Only one rotation can run at a time, so all buttons are disabled.
        buttons = re.findall(r'<button[^>]*data-action="rotate-job"[^>]*>', html, re.S)
        self.assertEqual(len(buttons), 2)
        self.assertTrue(all("disabled" in button for button in buttons))
        self.assertIn("Rotating, 1 job in progress", html)
        self.assertIn("Rotating now", html)

    def test_rotate_buttons_are_enabled_when_idle(self):
        html = self.page()
        buttons = re.findall(r'<button[^>]*data-action="rotate-job"[^>]*>', html, re.S)
        self.assertEqual(len(buttons), 2)
        self.assertFalse(any("disabled" in button for button in buttons))

    def test_rotate_all_is_available_with_confirmation_dialog(self):
        html = self.page()
        self.assertIn('data-open="m-rotate">Rotate all</button>', html)
        self.assertIn('id="m-rotate"', html)
        self.assertIn("2 secrets will be rotated now.", html)

    def test_empty_state_without_jobs(self):
        self.engine.rotation_jobs = []
        html = self.page()
        self.assertIn("No jobs yet.", html)
        self.assertNotIn('type="search"', html)
        self.assertRegex(html, r'data-open="m-rotate" disabled')

    def test_new_job_button_is_not_offered_until_it_works(self):
        self.assertNotIn("New job", self.page())

    def test_loads_scripts_and_has_no_inline_handlers(self):
        html = self.page()
        for name in ("http.js", "overlays.js", "table-filter.js", "rotate.js"):
            self.assertIn(f"js/{name}", html)
        self.assertNotRegex(html, r"\son(click|load|error|input)=")

    def test_job_names_are_escaped(self):
        self.engine.rotation_jobs = [
            {
                "name": '<img src=x onerror="alert(1)">',
                "secret_id": "<b>id</b>",
                "provider": "p",
                "rotator": "r",
            }
        ]
        html = self.page()
        self.assertNotIn("<img src=x", html)
        self.assertNotIn("<b>id</b>", html)
        self.assertIn("&lt;img src=x", html)

    def test_job_names_containing_slashes_link_to_a_working_page(self):
        self.engine.rotation_jobs = [
            {"name": "prod/db", "secret_id": "db", "provider": "p", "rotator": "r"}
        ]
        self.assertIn('href="/jobs/prod/db"', self.page())
        resp = self.client.get("/jobs/prod/db")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("<h1>prod/db</h1>", resp.get_data(as_text=True))

    def test_requires_login(self):
        self.client.get("/logout")
        anonymous = self.client.application.test_client()
        self.assertEqual(anonymous.get("/jobs").status_code, 302)


if __name__ == "__main__":
    unittest.main()

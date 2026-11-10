"""Tests for the job editor markup: the New job / Edit drawer, Pause,
Delete, Reset and Restore controls on the Jobs, job and Overview pages."""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import schedule
import yaml
from werkzeug.security import generate_password_hash

from secret_rotator.audit_log import AuditLog
from secret_rotator.config.settings import Settings
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.job_admin import JobAdminService
from secret_rotator.job_status import JobStatusService
from secret_rotator.providers.base import SecretProvider
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.rotators.base import SecretRotator
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

BASE = {
    "rotation": {"schedule": "daily"},
    "jobs": [
        {
            "name": "db",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "db_password",
            "schedule": "weekly",
            "notification": True,
        },
        {
            "name": "api",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "api_key",
        },
    ],
}
USERNAME = "editor-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)


class Provider(SecretProvider):
    def __init__(self, name):
        super().__init__(name=name, config={})

    def get_secret(self, secret_id):
        return "old"

    def update_secret(self, secret_id, new_value):
        return True

    def validate_connection(self):
        return True


class Rotator(SecretRotator):
    def __init__(self, name):
        super().__init__(name=name, config={})

    def generate_new_secret(self):
        return "new"

    def validate_secret(self, value):
        return True


class TestJobEditorPages(unittest.TestCase):
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
        schedule.clear()
        self.addCleanup(schedule.clear)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        yaml_path = self.dir / "config.yaml"
        yaml_path.write_text(yaml.safe_dump(BASE))
        env = patch.dict(
            os.environ, {"SECRET_ROTATOR_DASHBOARD_SETTINGS": str(self.dir / "dash.json")}
        )
        env.start()
        self.addCleanup(env.stop)
        self.settings = Settings(config_path=str(yaml_path))

        self.audit = AuditLog(audit_file=str(self.dir / "audit.log"))
        self.engine = RotationEngine()
        self.engine.backup_manager = MagicMock()
        self.engine.backup_manager.count_backups.return_value = 0
        self.engine.backup_manager.list_backup_page.return_value = {
            "backups": [],
            "total": 0,
            "page": 1,
            "per_page": 5,
            "pages": 1,
        }
        for name in ("file_storage", "aws"):
            self.engine.register_provider(Provider(name))
        for name in ("password_gen", "api_key_gen"):
            self.engine.register_rotator(Rotator(name))
        self.engine.replace_rotation_jobs(self.settings.get("jobs"))
        self.admin = JobAdminService(self.engine, self.settings, audit=self.audit)

        app = create_app(self.engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.job_admin = self.admin
        app.job_status = JobStatusService(
            self.engine,
            audit=self.audit,
            source_of=lambda n: self.settings.overrides.item_source("jobs", n),
        )
        self.client = app.test_client()
        limiter.reset()
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def get(self, url, status=200):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status, url)
        return resp.get_data(as_text=True)

    def job_data(self, html):
        raw = re.search(
            r'<script type="application/json" id="job-data">(.*?)</script>', html, re.S
        ).group(1)
        return json.loads(raw)

    # ---- the drawer ----

    def test_drawer_offers_the_configured_providers_rotators_and_schedules(self):
        html = self.get("/jobs")
        drawer = html[html.index('id="d-job"') :]
        self.assertIn('<option value="aws">aws</option>', drawer)
        self.assertIn('<option value="file_storage">file_storage</option>', drawer)
        self.assertIn('<option value="api_key_gen">api_key_gen</option>', drawer)
        for schedule_name, label in (
            ("every_30_minutes", "Every 30 minutes"),
            ("monthly", "Monthly"),
        ):
            self.assertIn(f'<option value="{schedule_name}">{label}</option>', drawer)
        self.assertIn("Follow the default (daily)", drawer)

    def test_drawer_is_an_accessible_dialog_with_labelled_fields(self):
        html = self.get("/jobs")
        self.assertRegex(
            html,
            r'id="d-job" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="d-job-title"',
        )
        for field in ("job-name", "job-secret-id", "job-provider", "job-rotator", "job-schedule"):
            self.assertRegex(html, rf'<label class="field" for="{field}">')
        for name in ("name", "secret_id", "provider", "rotator", "schedule"):
            self.assertIn(f'id="err-{name}"', html)
        self.assertIn('id="job-form-error" role="alert"', html)
        self.assertIn('aria-labelledby="job-notification-label"', html)

    def test_drawer_carries_the_settings_revision_for_conflict_detection(self):
        self.assertIn('data-revision="0"', self.get("/jobs"))
        self.admin.set_paused("api", True, "alice")
        self.assertIn('data-revision="1"', self.get("/jobs"))

    def test_save_is_disabled_when_nothing_could_be_chosen(self):
        self.engine.providers.clear()
        html = self.get("/jobs")
        self.assertIn("No providers are configured.", html)
        self.assertRegex(html, r'id="job-save" disabled')

    def test_hostile_provider_names_are_escaped(self):
        self.engine.providers['"><script>alert(1)</script>'] = Provider("x")
        html = self.get("/jobs")
        self.assertNotIn("<script>alert(1)</script>", html)

    # ---- the Jobs page ----

    def test_new_job_button_and_scripts(self):
        html = self.get("/jobs")
        self.assertIn('data-action="new-job">New job</button>', html)
        self.assertIn("js/job-admin.js", html)
        self.assertIn("css/job.css", html)
        self.assertNotRegex(html, r"\son(click|load|error|input|submit)=")

    def test_paused_jobs_show_a_paused_badge_instead_of_their_status(self):
        self.admin.set_paused("db", True, "alice")
        html = self.get("/jobs")
        self.assertIn(
            '<span class="badge" title="Paused: scheduled rotation is off">Paused</span>', html
        )

    def test_rotate_all_leaves_out_paused_jobs(self):
        self.admin.set_paused("db", True, "alice")
        html = self.get("/jobs")
        self.assertIn("1 secret will be rotated now.", html)
        self.admin.set_paused("api", True, "alice")
        html = self.get("/jobs")
        self.assertRegex(html, r'data-open="m-rotate" disabled')

    def test_hidden_config_jobs_can_be_restored(self):
        self.assertNotIn("Hidden jobs", self.get("/jobs"))
        self.admin.delete("db", "alice")
        html = self.get("/jobs")
        self.assertIn("Hidden jobs", html)
        self.assertIn("config.yaml itself is not changed", html)
        self.assertRegex(html, r'data-action="restore-job" data-job="db">Restore</button>')
        self.assertNotIn('href="/jobs/db"', html)

    def test_empty_state_points_at_new_job(self):
        self.engine.replace_rotation_jobs([])
        self.assertIn("Use <b>New job</b>", self.get("/jobs"))

    # ---- the job page ----

    def test_job_page_controls_for_a_running_schedule(self):
        html = self.get("/jobs/db")
        self.assertIn('data-action="edit-job">Edit</button>', html)
        self.assertRegex(html, r'data-action="pause-job" data-job="db">Pause</button>')
        self.assertNotIn('data-action="resume-job"', html)
        self.assertNotIn(">Paused<", html)

    def test_job_page_controls_for_a_paused_job(self):
        self.admin.set_paused("db", True, "alice")
        html = self.get("/jobs/db")
        self.assertRegex(html, r'data-action="resume-job" data-job="db">Resume</button>')
        self.assertNotIn('data-action="pause-job"', html)
        self.assertIn('title="Paused: scheduled rotation is off">Paused</span>', html)
        self.assertIn('<dt>Next run</dt><dd><span class="muted">Paused</span></dd>', html)

    def test_source_is_explained(self):
        self.assertIn("<dt>Source</dt>", self.get("/jobs/db"))
        self.assertRegex(self.get("/jobs/db"), r"<dt>Source</dt>\s*<dd>\s*config.yaml\s*</dd>")
        self.admin.set_paused("db", True, "alice")
        self.assertRegex(self.get("/jobs/db"), r"config.yaml, changed in the dashboard")
        self.admin.create(
            {"name": "new", "provider": "aws", "rotator": "api_key_gen", "secret_id": "n"}, "alice"
        )
        self.assertIn("Added in the dashboard", self.get("/jobs/new"))

    def test_reset_is_offered_only_for_changed_config_jobs(self):
        unchanged = self.get("/jobs/db")
        self.assertNotIn('data-open="m-reset"', unchanged)
        self.assertNotIn('id="m-reset"', unchanged)
        self.admin.set_paused("db", True, "alice")
        changed = self.get("/jobs/db")
        self.assertIn('data-open="m-reset">Reset to config.yaml</button>', changed)
        self.assertIn('id="m-reset"', changed)
        self.admin.create(
            {"name": "new", "provider": "aws", "rotator": "api_key_gen", "secret_id": "n"}, "alice"
        )
        self.assertNotIn('data-open="m-reset"', self.get("/jobs/new"))

    def test_delete_confirmation_explains_what_will_happen(self):
        config_job = self.get("/jobs/db")
        self.assertIn("which is not changed: the job is only hidden", config_job)
        self.assertIn("Existing backups are kept.", config_job)
        self.admin.create(
            {"name": "new", "provider": "aws", "rotator": "api_key_gen", "secret_id": "n"}, "alice"
        )
        dashboard_job = self.get("/jobs/new")
        self.assertNotIn("only hidden", dashboard_job)
        self.assertIn("and be removed", dashboard_job)

    def test_confirmation_dialogs_are_accessible_and_target_the_job(self):
        self.admin.set_paused("db", True, "alice")
        html = self.get("/jobs/db")
        for dialog, title in (("m-delete", "m-delete-title"), ("m-reset", "m-reset-title")):
            self.assertRegex(
                html,
                rf'id="{dialog}" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="{title}"',
            )
        self.assertRegex(html, r'data-action="confirm-delete" data-job="db"')
        self.assertRegex(html, r'data-action="confirm-reset" data-job="db"')

    def test_job_data_blob_describes_the_job_for_the_editor(self):
        data = self.job_data(self.get("/jobs/db"))
        self.assertEqual(
            data,
            {
                "name": "db",
                "provider": "file_storage",
                "rotator": "password_gen",
                "secret_id": "db_password",
                "schedule": "weekly",
                "notification": True,
                "source": "config",
            },
        )
        api = self.job_data(self.get("/jobs/api"))
        self.assertIsNone(api["schedule"])
        self.assertIsNone(api["notification"])

    def test_job_data_blob_cannot_break_out_of_its_script_tag(self):
        self.admin.create(
            {
                "name": "evil",
                "provider": "aws",
                "rotator": "api_key_gen",
                "secret_id": "</script><script>alert(1)</script>",
            },
            "alice",
        )
        html = self.get("/jobs/evil")
        self.assertNotIn("</script><script>alert(1)", html)
        self.assertEqual(self.job_data(html)["secret_id"], "</script><script>alert(1)</script>")

    def test_job_page_loads_the_editor_script_and_drawer(self):
        html = self.get("/jobs/db")
        self.assertIn("js/job-admin.js", html)
        self.assertIn('id="d-job"', html)
        self.assertNotRegex(html, r"\son(click|load|error|input|submit)=")

    # ---- the Overview ----

    def test_overview_mentions_paused_jobs_and_excludes_them_from_rotate_all(self):
        self.admin.set_paused("db", True, "alice")
        html = self.get("/")
        self.assertIn("2 jobs (1 paused)", html)
        self.assertIn("1 secret will be rotated now.", html)
        self.assertIn(">Paused</span>", html)

    def test_overview_rotate_all_is_disabled_when_everything_is_paused(self):
        self.admin.set_paused("db", True, "alice")
        self.admin.set_paused("api", True, "alice")
        self.assertRegex(self.get("/"), r'data-open="m-rotate" disabled')


if __name__ == "__main__":
    unittest.main()

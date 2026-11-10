"""Tests for the Settings page."""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from werkzeug.security import generate_password_hash

from secret_rotator.audit_log import AuditLog
from secret_rotator.config.settings import Settings
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.settings_admin import SettingsAdminService
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

USERNAME = "settings-page-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)
SECRET = "AKIA-NEVER-IN-THE-PAGE"


class TestSettingsPage(unittest.TestCase):
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
        self.dir = Path(tmp.name)
        self.config = {
            "rotation": {"schedule": "weekly", "backup_old_secrets": True},
            "backup": {"remote_backup": {"enabled": False, "bucket": "from-config"}},
            "web": {"host": "0.0.0.0"},
        }
        self.yaml_path = self.dir / "config.yaml"
        self.yaml_path.write_text(yaml.safe_dump(self.config))
        env = patch.dict(
            os.environ, {"SECRET_ROTATOR_DASHBOARD_SETTINGS": str(self.dir / "dash.json")}
        )
        env.start()
        self.addCleanup(env.stop)
        self.settings = Settings(config_path=str(self.yaml_path))
        self.audit = AuditLog(audit_file=str(self.dir / "audit.log"))
        self.engine = RotationEngine()
        self.engine.backup_manager = MagicMock()
        self.admin = SettingsAdminService(self.engine, self.settings, audit=self.audit)

        app = create_app(self.engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.settings_admin = self.admin
        self.client = app.test_client()
        limiter.reset()
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def page(self):
        resp = self.client.get("/settings")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def update(self, changes=None, reset=None):
        return self.admin.update(changes or {}, reset or [], "alice")

    # ---- structure ----

    def test_sections_nav_and_panels(self):
        html = self.page()
        self.assertIn("<h1>Settings</h1>", html)
        for key, label in (
            ("rotation", "Rotation"),
            ("security", "Security &amp; encryption"),
            ("backups", "Backups &amp; retention"),
            ("remote", "Remote backup"),
            ("distributed", "Distributed mode"),
            ("web", "Web &amp; access"),
            ("logging", "Logging"),
            ("notifications", "Notifications"),
        ):
            self.assertIn(f'<a href="#{key}" data-sec="{key}"', html)
            self.assertIn(f'id="s-{key}"', html)
            self.assertIn(f">{label}</a>", html)
        self.assertRegex(
            html, r'<a href="#rotation" data-sec="rotation" class="on" aria-current="true">'
        )

    def test_header_says_where_changes_are_saved(self):
        html = self.page()
        self.assertIn("config.yaml is not modified", html)
        self.assertIn("Applies immediately", html)

    def test_save_bar_exists_but_starts_hidden(self):
        html = self.page()
        self.assertRegex(html, r'<div class="savebar card" id="savebar"')
        self.assertNotIn('class="savebar card show"', html)
        self.assertIn(">Discard</button>", html)
        self.assertIn(">Save changes</button>", html)
        self.assertIn("Unsaved changes", html)

    def test_scripts_notice_and_no_inline_handlers(self):
        html = self.page()
        self.assertIn("js/settings.js", html)
        self.assertIn("css/settings.css", html)
        self.assertIn('id="notice"', html)
        self.assertNotRegex(html, r"\son(click|load|error|input|change|submit)=")

    def test_the_form_carries_the_revision_for_conflict_detection(self):
        self.assertIn('data-revision="0"', self.page())
        self.update({"web.port": 9090})
        self.assertIn('data-revision="1"', self.page())

    def test_nav_highlight_and_login(self):
        self.assertRegex(
            self.page(), r'<a href="/settings" class="on" aria-current="page">Settings</a>'
        )
        self.assertEqual(self.client.application.test_client().get("/settings").status_code, 302)

    # ---- controls ----

    def test_each_kind_gets_the_right_control(self):
        html = self.page()
        self.assertRegex(
            html,
            r'<input type="checkbox" id="c-rotation.backup_old_secrets" data-key="rotation.backup_old_secrets" data-kind="bool"[^>]* checked>',
        )
        self.assertRegex(
            html,
            r'<input class="input" type="number" step="1" min="1" max="65535"[^>]*id="c-web.port"[^>]*data-kind="int"',
        )
        self.assertRegex(
            html, r'<select class="input"[^>]*id="c-logging.level"[^>]*data-kind="choice"'
        )
        self.assertRegex(
            html,
            r'<input class="input" type="time"[^>]*id="c-backup.cleanup_time"[^>]*value="03:00"',
        )
        self.assertRegex(
            html, r'<select class="input"[^>]*id="c-rotation.schedule"[^>]*data-kind="schedule"'
        )
        self.assertRegex(
            html,
            r'<input class="input mono" type="text"[^>]*id="c-backup.remote_backup.bucket"[^>]*value="from-config"',
        )

    def test_current_values_and_originals(self):
        html = self.page()
        self.assertIn("data-original='\"weekly\"'", html)
        self.assertIn('<option value="weekly" selected>Weekly</option>', html)
        self.assertIn("data-original='true'", html)
        self.assertIn('value="0.0.0.0"', html)
        self.assertIn('<span class="muted unit">days</span>', html)

    def test_a_schedule_not_in_the_list_is_kept_as_an_option(self):
        self.update({"rotation.schedule": "every_5_minutes"})
        html = self.page()
        self.assertIn('<option value="every_5_minutes" selected>Every 5 minutes</option>', html)

    def test_every_control_is_labelled(self):
        html = self.page()
        self.assertIn('id="l-web.port"', html)
        self.assertIn('aria-labelledby="l-web.port"', html)
        self.assertIn('aria-describedby="err-web.port"', html)
        self.assertIn('id="err-web.port"', html)

    def test_help_text_is_shown(self):
        self.assertIn("Used by every job that does not have a schedule of its own.", self.page())

    # ---- restart markers ----

    def test_live_settings_say_so_and_others_have_a_hidden_restart_badge(self):
        html = self.page()
        row = re.search(
            r'<div class="set" data-row="rotation.schedule".*?</div>\s*<div class="ctl">',
            html,
            re.S,
        ).group(0)
        self.assertIn("Applies immediately", row)
        port = re.search(
            r'<div class="set" data-row="web.port".*?</div>\s*<div class="ctl">', html, re.S
        ).group(0)
        self.assertIn('class="badge warn restart-badge" hidden>Restart required', port)

    def test_a_pending_restart_is_shown_on_the_setting_and_in_a_banner(self):
        self.update({"web.port": 9090, "web.threads": 4})
        html = self.page()
        self.assertIn(
            '<span class="badge warn restart-badge" data-pending>Restart required</span>', html
        )
        self.assertIn("Restart the daemon to apply: Port, Server threads.", html)

    def test_no_banner_when_nothing_is_pending(self):
        self.assertNotIn("restart-banner", self.page())
        self.update({"backup.retention.days": 30})
        self.assertNotIn("restart-banner", self.page())

    # ---- sources and reset ----

    def test_dashboard_changes_offer_a_reset(self):
        html = self.page()
        self.assertNotIn("Changed in the dashboard", html)
        self.update({"web.port": 9090})
        html = self.page()
        self.assertIn("Changed in the dashboard", html)
        self.assertRegex(html, r'data-action="reset-setting">Reset</button>')

    # ---- read-only ----

    def test_read_only_settings_show_their_value_and_reason_without_a_control(self):
        html = self.page()
        self.assertRegex(
            html,
            r'<span class="mono readonly" title="[^"]*config.yaml[^"]*">data/\.master\.key</span>',
        )
        self.assertNotIn('id="c-security.encryption.master_key_file"', html)
        self.assertNotIn('id="c-logging.file"', html)
        self.assertIn("Change it in config.yaml.", html)

    # ---- credentials ----

    def test_credentials_are_never_in_the_page(self):
        self.update(
            {
                "backup.remote_backup.access_key_id": "AKIA1",
                "backup.remote_backup.secret_access_key": SECRET,
            }
        )
        html = self.page()
        self.assertNotIn(SECRET, html)
        self.assertNotIn("AKIA1", html)
        row = re.search(
            r'<div class="secret-ctl" data-key="backup.remote_backup.secret_access_key".*?</div>',
            html,
            re.S,
        ).group(0)
        self.assertIn('data-set="true"', row)
        self.assertIn(">Set</span>", row)
        self.assertIn("dashboard", row)
        self.assertIn('data-action="replace-secret"', row)
        self.assertIn('data-action="clear-secret"', row)
        self.assertRegex(
            row, r'<input class="input" type="password" autocomplete="new-password"[^>]* hidden'
        )

    def test_unset_credentials(self):
        html = self.page()
        row = re.search(
            r'<div class="secret-ctl" data-key="distributed.redis_url".*?</div>', html, re.S
        ).group(0)
        self.assertIn('data-set="false"', row)
        self.assertIn(">Not set</span>", row)
        self.assertIn(">Set…</button>", row)
        self.assertNotIn("clear-secret", row)

    def test_a_credential_from_config_yaml_shows_its_source_and_cannot_be_cleared(self):
        self.config["distributed"] = {"redis_url": "redis://:cfg-pw@cache/0"}
        self.yaml_path.write_text(yaml.safe_dump(self.config))
        self.settings.reload()
        html = self.page()
        self.assertNotIn("cfg-pw", html)
        row = re.search(
            r'<div class="secret-ctl" data-key="distributed.redis_url".*?</div>', html, re.S
        ).group(0)
        self.assertIn("config.yaml", row)
        self.assertNotIn("clear-secret", row)

    # ---- unavailable ----

    def test_unavailable_features_are_explained_not_faked(self):
        html = self.page()
        self.assertIn("Not available in this version", html)
        self.assertIn("Nothing in this version sends notifications", html)
        notifications = re.search(
            r'<section class="spanel" id="s-notifications".*?</section>', html, re.S
        ).group(0)
        self.assertNotIn("data-key=", notifications)
        self.assertNotIn("<input", notifications)
        for absent in (
            "c-rotation.timeout",
            "c-rotation.retry_attempts",
            "c-notifications.enabled",
        ):
            self.assertNotIn(absent, html)

    # ---- warnings and escaping ----

    def test_file_warnings_are_shown(self):
        self.update({"web.port": 9090})
        path = self.dir / "dash.json"
        data = json.loads(path.read_text())
        data["settings"]["backup.remote_backup.secret_access_key"] = "plain-text-value"
        path.write_text(json.dumps(data))
        html = self.page()
        self.assertIn("stored as plain text", html)
        self.assertNotIn("plain-text-value", html)

    def test_an_unreadable_settings_file_is_reported(self):
        (self.dir / "dash.json").write_text("{broken")
        self.assertIn("could not be read, so only config.yaml applies", self.page())

    def test_values_from_config_are_escaped(self):
        self.config["backup"]["remote_backup"]["bucket"] = '"><script>alert(1)</script>'
        self.yaml_path.write_text(yaml.safe_dump(self.config))
        self.settings.reload()
        html = self.page()
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_original_values_cannot_break_out_of_their_attribute(self):
        self.config["backup"]["remote_backup"]["bucket"] = "x' onfocus='alert(1)"
        self.yaml_path.write_text(yaml.safe_dump(self.config))
        self.settings.reload()
        html = self.page()
        self.assertNotRegex(html, r"data-original='[^']*' onfocus=")


if __name__ == "__main__":
    unittest.main()

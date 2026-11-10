"""Tests for the Providers and Rotators pages."""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from werkzeug.security import generate_password_hash

from secret_rotator import bootstrap
from secret_rotator.audit_log import AuditLog
from secret_rotator.component_admin import ComponentAdminService
from secret_rotator.config.settings import Settings
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.job_admin import JobAdminService
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

USERNAME = "components-page-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)


class TestComponentPages(unittest.TestCase):
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
        self.data = self.dir / "data"
        self.data.mkdir()
        config = {
            "dashboard": {"provider_root": str(self.data)},
            "security": {
                "encryption": {"enabled": False, "master_key_file": str(self.data / ".master.key")}
            },
            "providers": {
                "file_storage": {"type": "file", "file_path": str(self.data / "secrets.json")},
                "aws_secrets": {"type": "aws", "region": "us-east-1"},
            },
            "rotators": {
                "password_gen": {"type": "password", "length": 16},
                "api_key_gen": {"type": "api_key", "length": 32},
                "legacy_db": {"type": "database", "host": "db.internal.example.com"},
            },
            "jobs": [
                {
                    "name": "db",
                    "provider": "file_storage",
                    "rotator": "password_gen",
                    "secret_id": "db_password",
                }
            ],
        }
        yaml_path = self.dir / "config.yaml"
        yaml_path.write_text(yaml.safe_dump(config))
        env = patch.dict(
            os.environ, {"SECRET_ROTATOR_DASHBOARD_SETTINGS": str(self.data / "dash.json")}
        )
        env.start()
        self.addCleanup(env.stop)
        self.settings = Settings(config_path=str(yaml_path))
        patcher = patch("secret_rotator.bootstrap.settings", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.audit = AuditLog(audit_file=str(self.dir / "audit.log"))
        self.engine = RotationEngine()
        self.engine.backup_manager = MagicMock()
        bootstrap._setup_providers(self.engine)
        bootstrap._setup_rotators(self.engine)
        self.engine.replace_rotation_jobs(self.settings.get("jobs"))
        self.admin = ComponentAdminService(self.engine, self.settings, audit=self.audit)

        app = create_app(self.engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.component_admin = self.admin
        app.job_admin = JobAdminService(self.engine, self.settings, audit=self.audit)
        self.client = app.test_client()
        limiter.reset()
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def get(self, url):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, url)
        return resp.get_data(as_text=True)

    def blob(self, html):
        raw = re.search(
            r'<script type="application/json" id="component-data">(.*?)</script>', html, re.S
        ).group(1)
        return json.loads(raw)

    # ---- providers ----

    def test_providers_page_lists_each_provider(self):
        html = self.get("/providers")
        self.assertIn("<h1>Providers</h1>", html)
        self.assertIn("Where secrets are stored and written back.", html)
        self.assertRegex(html, r'<div class="reg" data-name="file_storage">')
        self.assertIn("<b>file_storage</b>", html)
        self.assertIn("File on disk", html)
        self.assertIn(f'<b class="mono">{self.data / "secrets.json"}</b>File path', html)
        self.assertRegex(html, r'<div class="reg" data-name="aws_secrets">')

    def test_provider_badges_and_usage(self):
        html = self.get("/providers")
        self.assertIn('<span class="badge ok">Available</span>', html)
        self.assertIn(">Unavailable</span>", html)
        self.assertIn("This type is not available in this version", html)
        self.assertIn("Used by 1 job", html)
        self.assertIn("Used by 0 jobs", html)

    def test_provider_actions(self):
        html = self.get("/providers")
        self.assertRegex(
            html, r'data-action="test-component" data-name="file_storage"[^>]*>Test</button>'
        )
        self.assertRegex(html, r'data-action="edit-component" data-name="file_storage"')
        self.assertRegex(
            html, r'data-action="test-component" data-name="aws_secrets"[^>]*disabled>Test</button>'
        )
        self.assertIn('data-action="new-component">Add provider</button>', html)
        self.assertNotIn("data-sample", html)

    def test_source_labels(self):
        self.assertIn("From config.yaml", self.get("/providers"))
        self.admin.create(
            "providers",
            {"name": "extra", "type": "file", "file_path": str(self.data / "e.json")},
            "alice",
        )
        self.admin.update(
            "providers",
            "file_storage",
            {"type": "file", "file_path": str(self.data / "moved.json")},
            "alice",
        )
        html = self.get("/providers")
        self.assertIn("Added in the dashboard", html)
        self.assertIn("config.yaml, changed in the dashboard", html)

    def test_provider_test_result_area_is_a_live_region(self):
        self.assertIn('data-test-result role="status"', self.get("/providers"))

    # ---- rotators ----

    def test_rotators_page_lists_settings_and_a_sample_slot(self):
        html = self.get("/rotators")
        self.assertIn("<h1>Rotators</h1>", html)
        self.assertIn("How each new secret value is generated.", html)
        self.assertIn('<div class="muted">Password</div>', html)
        self.assertIn("<b>16</b>Length", html)
        self.assertIn("a-z A-Z 0-9 symbols", html)
        self.assertIn('<b class="mono" data-sample>—</b>Sample', html)
        self.assertIn('data-action="new-component">Add rotator</button>', html)
        self.assertRegex(
            html, r'data-action="test-component" data-name="password_gen"[^>]*>Sample</button>'
        )

    def test_rotator_badges(self):
        html = self.get("/rotators")
        self.assertIn('<span class="badge acc">In use</span>', html)  # password_gen
        self.assertIn('<span class="badge">Not used</span>', html)  # api_key_gen
        self.assertIn(">Unavailable</span>", html)  # legacy_db

    def test_unavailable_rotators_show_no_connection_details(self):
        html = self.get("/rotators")
        self.assertNotIn("db.internal", html)
        self.assertRegex(html, r'data-name="legacy_db"[^>]*>\s*<div>\s*<b>legacy_db</b>')
        self.assertRegex(html, r'data-action="test-component" data-name="legacy_db"[^>]*disabled>')

    def test_rotator_drawer_has_the_sample_preview(self):
        html = self.get("/rotators")
        self.assertIn('id="sample-button"', html)
        self.assertIn("A random example of what these settings generate", html)
        self.assertNotIn('id="sample-box"', self.get("/providers"))

    # ---- shared ----

    def test_embedded_data_describes_items_and_types_for_the_form(self):
        data = self.blob(self.get("/rotators"))
        self.assertEqual((data["kind"], data["noun"], data["revision"]), ("rotators", "rotator", 0))
        items = {i["name"]: i for i in data["items"]}
        self.assertEqual(items["password_gen"]["fields"]["length"], 16)
        self.assertEqual(items["password_gen"]["used_by"], ["db"])
        self.assertFalse(items["legacy_db"]["supported"])
        self.assertEqual({t["type"] for t in data["types"]}, {"password", "api_key", "jwt_secret"})
        self.assertNotIn("db.internal", json.dumps(data))

    def test_embedded_data_cannot_break_out_of_its_script_tag(self):
        self.engine.providers["</script><script>alert(1)</script>"] = self.engine.providers[
            "file_storage"
        ]
        self.settings.overrides.set_item(
            "providers",
            "</script><script>alert(1)</script>",
            {"type": "file", "file_path": str(self.data / "x.json")},
            "alice",
        )
        html = self.get("/providers")
        self.assertNotIn("</script><script>alert(1)", html)
        names = [i["name"] for i in self.blob(html)["items"]]
        self.assertIn("</script><script>alert(1)</script>", names)

    def test_drawer_and_confirm_dialog_are_accessible(self):
        html = self.get("/providers")
        self.assertRegex(
            html,
            r'id="d-component" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="d-component-title"',
        )
        self.assertRegex(
            html,
            r'id="m-component-confirm" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="m-component-confirm-title"',
        )
        for field in ("component-name", "component-type"):
            self.assertRegex(html, rf'<label class="field" for="{field}">')
        self.assertIn('id="component-error" role="alert"', html)
        self.assertIn('id="component-delete" data-action="delete-component" hidden', html)

    def test_hidden_items_can_be_restored(self):
        self.assertNotIn("Hidden providers", self.get("/providers"))
        self.admin.delete("providers", "aws_secrets", "alice")
        html = self.get("/providers")
        self.assertIn("Hidden providers", html)
        self.assertRegex(
            html, r'data-action="restore-component" data-name="aws_secrets">Restore</button>'
        )
        self.assertNotIn('data-name="aws_secrets"><div', html)

    def test_empty_state(self):
        for name in ("aws_secrets",):
            self.admin.delete("providers", name, "alice")
        self.engine.providers.clear()
        self.settings.overrides.remove_item("providers", "file_storage", "alice")
        self.assertIn("No providers yet. Use <b>Add provider</b>", self.get("/providers"))

    def test_scripts_notice_and_no_inline_handlers(self):
        for url in ("/providers", "/rotators"):
            html = self.get(url)
            self.assertIn("js/components.js", html)
            self.assertIn("css/registry.css", html)
            self.assertIn('id="notice"', html)
            self.assertNotRegex(html, r"\son(click|load|error|input|submit)=")

    def test_sidebar_highlights_the_current_page(self):
        self.assertRegex(
            self.get("/providers"),
            r'<a href="/providers" class="on" aria-current="page">Providers</a>',
        )
        self.assertRegex(
            self.get("/rotators"),
            r'<a href="/rotators" class="on" aria-current="page">Rotators</a>',
        )

    def test_requires_login(self):
        anonymous = self.client.application.test_client()
        for url in ("/providers", "/rotators"):
            self.assertEqual(anonymous.get(url).status_code, 302)


if __name__ == "__main__":
    unittest.main()

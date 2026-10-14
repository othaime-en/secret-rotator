"""Tests for the Backups page."""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from secret_rotator.backup_manager import BackupManager
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

USERNAME = "backups-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)


class TestBackupsPage(unittest.TestCase):
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
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.manager = BackupManager(backup_dir=str(self.dir), encrypt_backups=False)

        engine = RotationEngine()
        engine.backup_manager = self.manager
        app = create_app(engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()
        limiter.reset()
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def write(self, secret_id, stamp, encrypted=True):
        name = f"{secret_id}_{stamp}.json"
        (self.dir / name).write_text(
            json.dumps(
                {
                    "secret_id": secret_id,
                    "timestamp": stamp,
                    "old_value": "OLD-SECRET-VALUE",
                    "new_value": "NEW-SECRET-VALUE",
                    "backup_created": "2026-10-12T04:00:00",
                    "encrypted": encrypted,
                }
            )
        )
        return name

    def seed(self, count):
        for day in range(1, count + 1):
            self.write("db_password", f"202609{day:02d}_040000_000000")

    def page(self, query=""):
        resp = self.client.get("/backups" + query)
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    # ---- listing ----

    def test_lists_backups_newest_first(self):
        self.write("db_password", "20261010_040000_000000")
        self.write("stripe_key", "20261012_040000_000000")
        html = self.page()
        self.assertLess(
            html.index("stripe_key_20261012_040000_000000.json"),
            html.index("db_password_20261010_040000_000000.json"),
        )
        self.assertIn('<td class="cell-t">stripe_key</td>', html)

    def test_encryption_badges(self):
        self.write("a", "20261001_040000_000000", encrypted=True)
        self.write("b", "20261002_040000_000000", encrypted=False)
        (self.dir / "c_20261003_040000_000000.json").write_text("{broken")
        html = self.page()
        self.assertIn('<span class="badge ok">Encrypted</span>', html)
        self.assertIn('<span class="badge warn">Unencrypted</span>', html)
        self.assertIn('<span class="badge">Unreadable</span>', html)

    def test_unreadable_backup_cannot_be_restored(self):
        (self.dir / "c_20261003_040000_000000.json").write_text("{broken")
        html = self.page()
        restore = re.search(r'<button[^>]*data-open="m-restore"[^>]*>', html, re.S).group(0)
        self.assertIn("disabled", restore)

    def test_row_buttons_carry_the_file_and_secret(self):
        name = self.write("db_password", "20261001_040000_000000")
        html = self.page()
        self.assertRegex(
            html, rf'data-action="view-backup"\s+data-file="{name}" data-secret="db_password"'
        )
        self.assertRegex(
            html, rf'data-open="m-restore"\s+data-file="{name}" data-secret="db_password"'
        )
        self.assertIn(f'aria-label="Restore backup {name}"', html)

    def test_no_secret_values_or_server_paths_in_the_page(self):
        self.write("db_password", "20261001_040000_000000")
        html = self.page()
        self.assertNotIn("SECRET-VALUE", html)
        self.assertNotIn(str(self.dir), html)

    # ---- filtering ----

    def test_filter_form_is_a_real_get_form(self):
        html = self.page()
        self.assertRegex(html, r'<form class="row" method="get" action="/backups" role="search"')
        self.assertIn('name="q"', html)
        self.assertIn('aria-label="Filter by secret ID"', html)
        self.assertNotIn(">Clear</a>", html)

    def test_filtering_keeps_the_query_and_offers_clear(self):
        self.write("db_password", "20261001_040000_000000")
        self.write("stripe_key", "20261002_040000_000000")
        html = self.page("?q=stripe")
        self.assertIn('value="stripe"', html)
        self.assertIn("stripe_key_20261002", html)
        self.assertNotIn("db_password_20261001", html)
        self.assertIn('href="/backups">Clear</a>', html)

    def test_no_match_message_escapes_the_query(self):
        self.write("db_password", "20261001_040000_000000")
        html = self.page("?q=%3Cscript%3Ealert(1)%3C/script%3E")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("No backups match “&lt;script&gt;", html)

    def test_empty_state(self):
        self.assertIn("No backups yet.", self.page())

    # ---- pagination ----

    def test_pagination_summary_and_links(self):
        self.seed(30)
        first = self.page()
        self.assertIn("Showing 1–25 of 30", first)
        self.assertIn('rel="next"', first)
        self.assertIn('href="/backups?page=2"', first)
        self.assertRegex(first, r'<span class="btn sm" aria-disabled="true">Previous</span>')

        second = self.page("?page=2")
        self.assertIn("Showing 26–30 of 30", second)
        self.assertIn('rel="prev"', second)
        self.assertIn('href="/backups"', second)  # page 1 link has no ?page=1
        self.assertRegex(second, r'<span class="btn sm" aria-disabled="true">Next</span>')

    def test_pagination_links_keep_the_filter(self):
        self.seed(30)
        html = self.page("?q=db_pass")
        self.assertIn('href="/backups?q=db_pass&amp;page=2"', html)

    def test_junk_or_out_of_range_page_numbers_are_safe(self):
        self.seed(30)
        self.assertIn("Showing 1–25 of 30", self.page("?page=abc"))
        self.assertIn("Showing 1–25 of 30", self.page("?page=-3"))
        self.assertIn("Showing 26–30 of 30", self.page("?page=999"))

    # ---- overlays and scripts ----

    def test_drawer_and_dialog_are_accessible(self):
        html = self.page()
        for overlay, title in (("d-backup", "d-backup-title"), ("m-restore", "m-restore-title")):
            self.assertRegex(
                html,
                rf'id="{overlay}" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="{title}"',
            )
            self.assertIn(f'id="{title}"', html)
        self.assertIn('aria-label="Close"', html)

    def test_values_are_masked_with_no_reveal_control(self):
        html = self.page()
        self.assertNotIn("Reveal", html)
        self.assertIn("Secret values are masked", html)

    def test_restore_dialog_warns_and_confirms(self):
        html = self.page()
        self.assertIn("Restore backup?", html)
        self.assertIn("Services using the current secret may lose access.", html)
        self.assertIn('data-action="confirm-restore"', html)

    def test_scripts_loaded_and_no_inline_handlers(self):
        html = self.page()
        for name in ("http.js", "flash.js", "overlays.js", "time.js", "backups.js"):
            self.assertIn(f"js/{name}", html)
        self.assertNotRegex(html, r"\son(click|load|error|input)=")

    def test_requires_login(self):
        anonymous = self.client.application.test_client()
        self.assertEqual(anonymous.get("/backups").status_code, 302)


if __name__ == "__main__":
    unittest.main()

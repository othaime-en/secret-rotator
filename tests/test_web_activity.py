"""Tests for the Activity page."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from werkzeug.security import generate_password_hash

from secret_rotator.activity import ActivityService
from secret_rotator.audit_log import AuditLog
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

USERNAME = "activity-page-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)
NOW = datetime.now(timezone.utc).replace(microsecond=0)


class TestActivityPage(unittest.TestCase):
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
        self.path = Path(tmp.name) / "audit.log"
        self.service = ActivityService(audit=AuditLog(audit_file=str(self.path)))

        app = create_app(RotationEngine())
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.activity = self.service
        self.client = app.test_client()
        limiter.reset()
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def add(self, minutes_ago, action, success=True, actor="system", secret_id=None, **details):
        event = {
            "timestamp": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
            "action": action,
            "actor": actor,
            "secret_id": secret_id,
            "success": success,
            "details": details,
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(event) + "\n")

    def page(self, query=""):
        resp = self.client.get("/activity" + query)
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def seed(self):
        self.add(50, "login", actor="alice")
        self.add(40, "rotate", job="db_job", secret_id="db")
        self.add(30, "rotate", success=False, job="api_job", secret_id="api", reason="timeout")
        self.add(20, "login_failed", success=False, actor="mallory")
        self.add(10, "verify_backups", success=False, total=10, verified=8, failed=2)

    # ---- log lines ----

    def test_lines_read_oldest_to_newest(self):
        self.seed()
        html = self.page()
        order = [
            html.index(m)
            for m in (
                "alice signed in",
                "db_job rotated",
                "api_job rotation failed",
                "Failed sign-in for mallory",
            )
        ]
        self.assertEqual(order, sorted(order))

    def test_level_labels_and_colours(self):
        self.seed()
        html = self.page()
        self.assertIn(
            '<span class="lv i">INFO</span> <span class="msg">alice signed in</span>', html
        )
        self.assertIn('<span class="lv s">OK</span> <span class="msg">db_job rotated</span>', html)
        self.assertIn(
            '<span class="lv e">ERROR</span> <span class="msg">api_job rotation failed: timeout</span>',
            html,
        )
        self.assertIn(
            '<span class="lv w">WARN</span> <span class="msg">Failed sign-in for mallory</span>',
            html,
        )
        self.assertIn("Backup verification: 8 of 10 backups verified", html)

    def test_lines_carry_their_sequence_number_and_a_utc_fallback_time(self):
        self.add(5, "rotate", job="db_job")
        html = self.page()
        self.assertIn('data-seq="1"', html)
        stamp = (NOW - timedelta(minutes=5)).isoformat()
        self.assertIn(f'<time datetime="{stamp}" data-fmt="logstamp">', html)

    def test_hostile_text_is_escaped(self):
        self.add(5, "login_failed", success=False, actor="<img src=x onerror=alert(1)>")
        html = self.page()
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img src=x", html)

    def test_log_pane_is_an_accessible_scrollable_region(self):
        html = self.page()
        self.assertRegex(
            html, r'<div class="log" id="log" role="log" aria-label="Activity log" tabindex="0"'
        )

    def test_no_secret_values_are_shown(self):
        self.add(5, "rotate", job="x", old_value="SECRET-OLD", new_value="SECRET-NEW")
        self.assertNotIn("SECRET-", self.page())

    # ---- filters ----

    def test_level_switch_uses_real_links_and_marks_the_current_one(self):
        html = self.page()
        self.assertIn('href="/activity" class="on" aria-current="true">All</a>', html)
        self.assertIn('href="/activity?level=info">Info</a>', html)
        self.assertIn('href="/activity?level=warning">Warnings</a>', html)
        self.assertIn('href="/activity?level=error">Errors</a>', html)

    def test_level_filter_limits_the_lines(self):
        self.seed()
        html = self.page("?level=error")
        self.assertIn("api_job rotation failed", html)
        self.assertNotIn("alice signed in", html)
        self.assertIn(
            'href="/activity?level=error" class="on" aria-current="true">Errors</a>', html
        )

    def test_unknown_level_falls_back_to_all(self):
        self.seed()
        html = self.page("?level=debug")
        self.assertIn("alice signed in", html)
        self.assertIn('class="on" aria-current="true">All</a>', html)

    def test_search_form_keeps_level_and_query(self):
        self.seed()
        html = self.page("?level=error&q=timeout")
        self.assertIn('<input type="hidden" name="level" value="error">', html)
        self.assertIn('value="timeout"', html)
        self.assertIn(">Clear</a>", html)
        self.assertIn("api_job rotation failed", html)

    def test_level_links_keep_the_search(self):
        html = self.page("?q=db")
        self.assertIn('href="/activity?level=error&amp;q=db">Errors</a>', html)

    def test_empty_messages(self):
        self.assertIn("No activity yet.", self.page())
        self.add(5, "rotate", job="x")
        self.assertIn("No activity matches.", self.page("?q=zzz"))

    # ---- paging ----

    def seed_many(self, count):
        for i in range(count):
            self.add(count - i, "rotate", job=f"job{i:03d}")

    def test_pagination_links_and_summary(self):
        self.seed_many(120)
        first = self.page()
        self.assertIn("Page 1 of 2 · 120 events", first)
        self.assertIn('rel="next"', first)
        self.assertIn('href="/activity?page=2">Older</a>', first)
        self.assertRegex(first, r'<span class="btn sm" aria-disabled="true">Newer</span>')
        self.assertIn("job119 rotated", first)
        self.assertNotIn("job000 rotated", first)

        second = self.page("?page=2")
        self.assertIn("Page 2 of 2", second)
        self.assertIn("job000 rotated", second)
        self.assertIn('href="/activity">Newer</a>', second)
        self.assertRegex(second, r'<span class="btn sm" aria-disabled="true">Older</span>')

    def test_junk_pages_are_safe(self):
        self.seed_many(120)
        self.assertIn("Page 1 of 2", self.page("?page=abc"))
        self.assertIn("Page 2 of 2", self.page("?page=999"))

    def test_single_page_has_no_pagination(self):
        self.seed()
        self.assertNotIn('aria-label="Pagination"', self.page())

    # ---- live view ----

    def test_live_toggle_and_data_on_the_newest_page(self):
        self.seed()
        html = self.page()
        self.assertRegex(
            html, r'<button type="button" class="badge acc" id="live-toggle" aria-pressed="true"'
        )
        self.assertIn('data-latest="5"', html)
        self.assertIn('data-live="true"', html)

    def test_live_data_carries_the_filters(self):
        self.seed()
        html = self.page("?level=error&q=timeout")
        self.assertIn('data-level="error"', html)
        self.assertIn('data-q="timeout"', html)

    def test_older_pages_are_not_live(self):
        self.seed_many(120)
        html = self.page("?page=2")
        self.assertNotIn('id="live-toggle"', html)
        self.assertIn('data-live="false"', html)

    def test_latest_is_the_newest_event_even_when_filtered_out(self):
        self.add(20, "rotate", success=False, job="a")
        self.add(10, "login", actor="bob")
        self.assertIn('data-latest="2"', self.page("?level=error"))

    # ---- export ----

    def test_export_links_carry_the_filters(self):
        html = self.page("?level=error&q=timeout")
        self.assertIn('href="/api/activity/export?format=csv&amp;level=error&amp;q=timeout"', html)
        self.assertIn('href="/api/activity/export?format=json&amp;level=error&amp;q=timeout"', html)

    def test_unfiltered_export_links_are_plain(self):
        html = self.page()
        self.assertIn('href="/api/activity/export?format=csv"', html)

    # ---- misc ----

    def test_scripts_and_no_inline_handlers(self):
        html = self.page()
        for name in ("http.js", "time.js", "activity.js"):
            self.assertIn(f"js/{name}", html)
        self.assertNotRegex(html, r"\son(click|load|error|input)=")

    def test_requires_login(self):
        anonymous = self.client.application.test_client()
        self.assertEqual(anonymous.get("/activity").status_code, 302)

    def test_log_line_markup_matches_the_javascript_builder(self):
        # activity.js builds the same structure for live updates; this
        # guards the class names both sides depend on.
        self.add(5, "rotate", job="x")
        html = self.page()
        self.assertRegex(
            html,
            r'<div class="line" data-seq="1"><span class="t"><time .*?</time></span> <span class="lv s">OK</span> <span class="msg">x rotated</span></div>',
        )
        js = (
            Path(__file__).parent.parent / "src/secret_rotator/web/static/js/activity.js"
        ).read_text()
        for needle in ('"line"', '"t"', '"lv "', '"msg"', "data-seq", "logstamp"):
            self.assertIn(needle, js)


if __name__ == "__main__":
    unittest.main()

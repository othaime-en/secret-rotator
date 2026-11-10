"""Tests for AuditLog.read_events, ActivityService and the
/api/activity endpoints."""

import csv
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from secret_rotator.activity import (
    MAX_EXPORT_ROWS,
    MAX_MESSAGE_LENGTH,
    ActivityService,
)
from secret_rotator.audit_log import AuditLog
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ts(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


class AuditFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "audit.log"
        self.audit = AuditLog(audit_file=str(self.path))
        self.service = ActivityService(audit=self.audit)

    def add(self, minutes_ago, action, success=True, actor="system", secret_id=None, **details):
        event = {
            "timestamp": ts(minutes_ago),
            "action": action,
            "actor": actor,
            "secret_id": secret_id,
            "success": success,
            "details": details,
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(event) + "\n")


class TestReadEvents(AuditFixture):
    def test_missing_file_gives_no_events(self):
        self.assertEqual(self.audit.read_events(), [])

    def test_events_are_oldest_first_with_line_numbers(self):
        self.add(30, "rotate", job="a")
        self.add(20, "rotate", job="b")
        events = self.audit.read_events()
        self.assertEqual([e["seq"] for e in events], [1, 2])
        self.assertEqual(events[0]["details"]["job"], "a")

    def test_bad_lines_are_skipped_but_still_counted(self):
        self.add(30, "rotate", job="a")
        with open(self.path, "a") as f:
            f.write("not json\n\n[1, 2]\n" + json.dumps({"action": "x"}) + "\n")
        self.add(20, "rotate", job="b")
        self.assertEqual([e["seq"] for e in self.audit.read_events()], [1, 6])

    def test_get_recent_events_is_unchanged(self):
        self.add(30, "rotate", job="a")
        self.assertNotIn("seq", self.audit.get_recent_events()[0])


class TestEnrichment(AuditFixture):
    def one(self, **kwargs):
        self.add(1, **kwargs)
        return self.service.query()["events"][0]

    def test_event_shape_exposes_no_raw_details(self):
        event = self.one(action="rotate", secret_id="db", job="db_job", provider="aws")
        self.assertEqual(
            set(event),
            {"seq", "timestamp", "level", "action", "actor", "secret_id", "success", "message"},
        )
        self.assertNotIn("aws", json.dumps(event))

    def test_timestamps_are_utc(self):
        self.assertTrue(self.one(action="rotate", job="x")["timestamp"].endswith("+00:00"))

    def test_levels(self):
        cases = [
            (dict(action="rotate", job="x"), "info"),
            (dict(action="rotate", success=False, job="x", reason="boom"), "error"),
            (dict(action="restore", success=False, secret_id="x"), "error"),
            (dict(action="login", success=False, actor="bob"), "warning"),
            (dict(action="login_failed", success=False, actor="bob"), "warning"),
            (
                dict(action="verify_backups", success=False, total=10, verified=8, failed=2),
                "warning",
            ),
            (dict(action="verify_backups", success=False, reason="could not complete"), "error"),
            (dict(action="logout", actor="bob"), "info"),
        ]
        for kwargs, expected in cases:
            with self.subTest(kwargs=kwargs):
                self.setUp()
                self.assertEqual(self.one(**kwargs)["level"], expected)

    def test_messages(self):
        cases = [
            (dict(action="rotate", job="db_job"), "db_job rotated"),
            (
                dict(action="rotate", success=False, job="db_job", reason="timeout"),
                "db_job rotation failed: timeout",
            ),
            (dict(action="restore", secret_id="db"), "db restored from backup"),
            (
                dict(action="restore", success=False, secret_id="db", reason="no key"),
                "db restore failed: no key",
            ),
            (
                dict(action="verify_backups", total=48, verified=45, failed=3),
                "Backup verification: 45 of 48 backups verified",
            ),
            (dict(action="login", actor="alice"), "alice signed in"),
            (
                dict(action="login_failed", success=False, actor="mallory"),
                "Failed sign-in for mallory",
            ),
            (dict(action="logout", actor="alice"), "alice signed out"),
            (
                dict(action="export_activity", actor="alice", format="csv", rows=3),
                "alice exported the activity log (csv)",
            ),
            (dict(action="something_new", secret_id="x"), "something_new x"),
        ]
        for kwargs, expected in cases:
            with self.subTest(kwargs=kwargs):
                self.setUp()
                self.assertEqual(self.one(**kwargs)["message"], expected)

    def test_long_messages_are_clipped(self):
        message = self.one(action="rotate", success=False, job="x", reason="y" * 1000)["message"]
        self.assertEqual(len(message), MAX_MESSAGE_LENGTH)
        self.assertTrue(message.endswith("…"))


class TestQuery(AuditFixture):
    def seed(self):
        self.add(50, "login", actor="alice")
        self.add(40, "rotate", job="db_job", secret_id="db")
        self.add(30, "rotate", success=False, job="api_job", secret_id="api", reason="timeout")
        self.add(20, "login_failed", success=False, actor="mallory")
        self.add(10, "restore", secret_id="db", actor="alice")

    def test_newest_first_with_totals(self):
        self.seed()
        result = self.service.query()
        self.assertEqual([e["action"] for e in result["events"]][0], "restore")
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["latest_seq"], 5)

    def test_level_filter(self):
        self.seed()
        self.assertEqual(self.service.query(level="error")["total"], 1)
        self.assertEqual(self.service.query(level="warning")["total"], 1)
        self.assertEqual(self.service.query(level="info")["total"], 3)

    def test_unknown_level_raises(self):
        with self.assertRaises(ValueError):
            self.service.query(level="debug")

    def test_search_matches_action_actor_secret_and_message(self):
        self.seed()
        for needle, expected in (
            ("MALLORY", 1),
            ("api", 1),
            ("timeout", 1),
            ("restore", 1),
            ("db", 2),
            ("nothing", 0),
        ):
            with self.subTest(needle=needle):
                self.assertEqual(self.service.query(q=needle)["total"], expected)

    def test_level_and_search_combine(self):
        self.seed()
        self.assertEqual(self.service.query(level="info", q="db")["total"], 2)
        self.assertEqual(self.service.query(level="error", q="db")["total"], 0)

    def test_paging(self):
        for i in range(7):
            self.add(100 - i, "rotate", job=f"j{i}")
        first = self.service.query(page=1, per_page=3)
        last = self.service.query(page=3, per_page=3)
        self.assertEqual((first["total"], first["pages"]), (7, 3))
        self.assertEqual(len(first["events"]), 3)
        self.assertEqual(len(last["events"]), 1)
        self.assertEqual(last["events"][0]["message"], "j0 rotated")

    def test_paging_is_clamped(self):
        self.add(1, "rotate", job="x")
        self.assertEqual(self.service.query(page=99)["page"], 1)
        self.assertEqual(self.service.query(page=-2)["page"], 1)
        self.assertEqual(self.service.query(per_page=0)["per_page"], 1)
        self.assertEqual(self.service.query(per_page=10**6)["per_page"], 500)

    def test_empty_log(self):
        result = self.service.query()
        self.assertEqual((result["events"], result["total"], result["latest_seq"]), ([], 0, 0))

    def test_cache_refreshes_when_the_file_changes(self):
        self.add(5, "rotate", job="a")
        self.assertEqual(self.service.query()["total"], 1)
        with patch.object(self.audit, "read_events", wraps=self.audit.read_events) as spy:
            self.service.query()
            self.assertEqual(spy.call_count, 0)  # cached
            self.add(1, "rotate", job="b")
            self.assertEqual(self.service.query()["total"], 2)
            self.assertEqual(spy.call_count, 1)


class TestSince(AuditFixture):
    def test_returns_only_newer_events_newest_first(self):
        for i in range(5):
            self.add(50 - i, "rotate", job=f"j{i}")
        result = self.service.since(after=2)
        self.assertEqual([e["seq"] for e in result["events"]], [5, 4, 3])
        self.assertEqual(result["latest_seq"], 5)

    def test_nothing_new(self):
        self.add(5, "rotate", job="a")
        result = self.service.since(after=1)
        self.assertEqual(result, {"events": [], "latest_seq": 1})

    def test_latest_seq_counts_events_hidden_by_the_filter(self):
        self.add(5, "rotate", job="a")
        self.add(4, "login", actor="bob")
        result = self.service.since(after=0, level="error")
        self.assertEqual(result["events"], [])
        self.assertEqual(result["latest_seq"], 2)

    def test_latest_seq_below_after_signals_a_rotated_file(self):
        self.add(5, "rotate", job="a")
        self.assertLess(self.service.since(after=500)["latest_seq"], 500)

    def test_limit(self):
        for i in range(10):
            self.add(50 - i, "rotate", job=f"j{i}")
        self.assertEqual(len(self.service.since(after=0, limit=4)["events"]), 4)


class TestExport(AuditFixture):
    def test_rows_are_oldest_first(self):
        self.add(20, "rotate", job="a")
        self.add(10, "rotate", job="b")
        rows, truncated = self.service.export_rows()
        self.assertEqual([r["message"] for r in rows], ["a rotated", "b rotated"])
        self.assertFalse(truncated)

    def test_export_is_capped_to_the_newest_rows(self):
        with patch("secret_rotator.activity.MAX_EXPORT_ROWS", 3):
            for i in range(5):
                self.add(50 - i, "rotate", job=f"j{i}")
            rows, truncated = self.service.export_rows()
        self.assertTrue(truncated)
        self.assertEqual([r["message"] for r in rows], ["j2 rotated", "j3 rotated", "j4 rotated"])

    def test_cap_constant(self):
        self.assertEqual(MAX_EXPORT_ROWS, 10_000)

    def test_csv_columns_and_content(self):
        self.add(5, "rotate", job="db_job", secret_id="db")
        rows, _ = self.service.export_rows()
        parsed = list(csv.reader(io.StringIO(self.service.to_csv(rows))))
        self.assertEqual(
            parsed[0], ["timestamp", "level", "action", "actor", "secret_id", "success", "message"]
        )
        self.assertEqual(
            parsed[1][1:], ["info", "rotate", "system", "db", "True", "db_job rotated"]
        )

    def test_csv_neutralises_spreadsheet_formulas(self):
        self.add(5, "login_failed", success=False, actor='=HYPERLINK("http://evil","x")')
        self.add(4, "login_failed", success=False, actor="+cmd|' /C calc'!A0")
        rows, _ = self.service.export_rows()
        parsed = list(csv.reader(io.StringIO(self.service.to_csv(rows))))
        for row in parsed[1:]:
            self.assertTrue(row[3].startswith("'"), row[3])

    def test_csv_quotes_commas_and_newlines(self):
        self.add(5, "rotate", success=False, job="a", reason='line1\nline2, "quoted"')
        rows, _ = self.service.export_rows()
        parsed = list(csv.reader(io.StringIO(self.service.to_csv(rows))))
        self.assertEqual(len(parsed), 2)
        self.assertIn("line2", parsed[1][-1])

    def test_json_export(self):
        self.add(5, "rotate", job="db_job")
        rows, _ = self.service.export_rows()
        data = json.loads(self.service.to_json(rows))
        self.assertEqual(data[0]["message"], "db_job rotated")
        self.assertNotIn("seq", data[0])

    def test_recording_an_export_writes_an_audit_event(self):
        self.service.record_export("alice", "csv", 12)
        event = self.audit.read_events()[0]
        self.assertEqual(event["action"], "export_activity")
        self.assertEqual(event["actor"], "alice")
        self.assertEqual(event["details"], {"format": "csv", "rows": 12})

    def test_filename(self):
        self.assertRegex(ActivityService.export_filename("csv"), r"^activity-\d{8}-\d{6}\.csv$")


USERNAME = "activity-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)


class TestActivityApi(AuditFixture):
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
        super().setUp()
        DistributedLock.reset_local_locks()
        app = create_app(RotationEngine())
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.activity = self.service
        self.client = app.test_client()
        limiter.reset()
        self.add(30, "rotate", job="db_job", secret_id="db")
        self.add(20, "rotate", success=False, job="api_job", secret_id="api", reason="timeout")
        self.add(10, "login_failed", success=False, actor="mallory")

    def login(self):
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def test_endpoints_require_login(self):
        self.assertEqual(self.client.get("/api/activity").status_code, 401)
        self.assertEqual(self.client.get("/api/activity/export").status_code, 401)

    def test_paged_response(self):
        self.login()
        data = self.client.get("/api/activity?per_page=2").get_json()
        self.assertEqual((data["total"], data["pages"], len(data["events"])), (3, 2, 2))
        self.assertEqual(data["latest_seq"], 3)

    def test_filters(self):
        self.login()
        self.assertEqual(self.client.get("/api/activity?level=error").get_json()["total"], 1)
        self.assertEqual(self.client.get("/api/activity?q=mallory").get_json()["total"], 1)

    def test_after_mode(self):
        self.login()
        data = self.client.get("/api/activity?after=1").get_json()
        self.assertEqual(set(data), {"events", "latest_seq"})
        self.assertEqual([e["seq"] for e in data["events"]], [3, 2])

    def test_bad_parameters_are_400(self):
        self.login()
        for query in ("level=debug", "page=x", "per_page=x", "after=x", "after=-1"):
            self.assertEqual(self.client.get(f"/api/activity?{query}").status_code, 400, query)

    def test_export_csv(self):
        self.login()
        resp = self.client.get("/api/activity/export?level=error")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "text/csv")
        self.assertRegex(
            resp.headers["Content-Disposition"],
            r'^attachment; filename="activity-\d{8}-\d{6}\.csv"$',
        )
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["X-Export-Truncated"], "false")
        rows = list(csv.reader(io.StringIO(resp.get_data(as_text=True))))
        self.assertEqual(len(rows), 2)
        self.assertIn("api_job rotation failed: timeout", rows[1][-1])

    def test_export_json(self):
        self.login()
        resp = self.client.get("/api/activity/export?format=json")
        self.assertEqual(resp.mimetype, "application/json")
        self.assertRegex(resp.headers["Content-Disposition"], r"\.json\"$")
        self.assertEqual(len(json.loads(resp.get_data(as_text=True))), 3)

    def test_export_is_audited_with_the_user(self):
        self.login()
        self.client.get("/api/activity/export?format=json&level=error")
        event = [e for e in self.audit.read_events() if e["action"] == "export_activity"][0]
        self.assertEqual(event["actor"], USERNAME)
        self.assertEqual(event["details"], {"format": "json", "rows": 1})

    def test_export_bad_parameters(self):
        self.login()
        self.assertEqual(self.client.get("/api/activity/export?format=xml").status_code, 400)
        self.assertEqual(self.client.get("/api/activity/export?level=debug").status_code, 400)
        self.assertEqual(
            [e for e in self.audit.read_events() if e["action"] == "export_activity"], []
        )

    def test_export_is_rate_limited(self):
        self.login()
        codes = [self.client.get("/api/activity/export").status_code for _ in range(11)]
        self.assertEqual(codes[-1], 429)

    def test_no_secret_values_in_responses(self):
        self.login()
        self.add(5, "rotate", job="x", old_value="SECRET-OLD", new_value="SECRET-NEW")
        body = self.client.get("/api/activity").get_data(as_text=True)
        body += self.client.get("/api/activity/export").get_data(as_text=True)
        self.assertNotIn("SECRET-OLD", body)
        self.assertNotIn("SECRET-NEW", body)


if __name__ == "__main__":
    unittest.main()

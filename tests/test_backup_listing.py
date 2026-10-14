"""Tests for the paged backup listing (BackupManager.list_backup_page,
GET /api/backup-list) and the shared time helpers."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from secret_rotator.backup_manager import BackupManager
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.utils.timeutil import parse_time, to_utc_iso
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

USERNAME = "backup-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)


class ListingFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.manager = BackupManager(backup_dir=str(self.dir), encrypt_backups=False)

    def write(self, secret_id, stamp, encrypted=True, created="2026-10-12T04:00:00"):
        """Write a backup file named like BackupManager does. stamp is
        'YYYYMMDD_HHMMSS_micros'."""
        name = f"{secret_id}_{stamp}.json"
        data = {
            "secret_id": secret_id,
            "timestamp": stamp,
            "old_value": "OLD-SECRET-VALUE",
            "new_value": "NEW-SECRET-VALUE",
            "backup_created": created,
            "encrypted": encrypted,
        }
        (self.dir / name).write_text(json.dumps(data))
        return name


class TestParseBackupName(unittest.TestCase):
    def test_simple_name(self):
        self.assertEqual(
            BackupManager._parse_backup_name("db_20261012_040000_123456.json"),
            ("db", "20261012_040000_123456"),
        )

    def test_secret_ids_with_underscores(self):
        self.assertEqual(
            BackupManager._parse_backup_name("prod_db_password_20261012_040000_123456.json"),
            ("prod_db_password", "20261012_040000_123456"),
        )

    def test_nonconforming_name_sorts_last(self):
        self.assertEqual(BackupManager._parse_backup_name("notes.json"), ("notes", ""))
        self.assertEqual(
            BackupManager._parse_backup_name("a_b_c_d.json"),
            ("a_b_c_d", ""),
        )


class TestListBackupPage(ListingFixture):
    def test_newest_first_across_secrets(self):
        self.write("db", "20261010_040000_000001")
        self.write("api", "20261012_040000_000001")
        self.write("db", "20261011_040000_000001")
        files = [b["file"] for b in self.manager.list_backup_page()["backups"]]
        self.assertEqual(
            files,
            [
                "api_20261012_040000_000001.json",
                "db_20261011_040000_000001.json",
                "db_20261010_040000_000001.json",
            ],
        )

    def test_paging_windows_and_totals(self):
        for day in range(1, 8):
            self.write("db", f"202610{day:02d}_040000_000000")
        first = self.manager.list_backup_page(page=1, per_page=3)
        last = self.manager.list_backup_page(page=3, per_page=3)
        self.assertEqual((first["total"], first["pages"], first["page"]), (7, 3, 1))
        self.assertEqual(len(first["backups"]), 3)
        self.assertEqual(len(last["backups"]), 1)
        self.assertEqual(last["backups"][0]["file"], "db_20261001_040000_000000.json")

    def test_page_is_clamped(self):
        self.write("db", "20261001_040000_000000")
        self.assertEqual(self.manager.list_backup_page(page=99)["page"], 1)
        self.assertEqual(self.manager.list_backup_page(page=0)["page"], 1)
        self.assertEqual(self.manager.list_backup_page(page=-4)["page"], 1)

    def test_per_page_is_limited(self):
        self.assertEqual(self.manager.list_backup_page(per_page=0)["per_page"], 1)
        self.assertEqual(self.manager.list_backup_page(per_page=5000)["per_page"], 100)

    def test_empty_directory(self):
        result = self.manager.list_backup_page()
        self.assertEqual(result["backups"], [])
        self.assertEqual((result["total"], result["pages"], result["page"]), (0, 1, 1))

    def test_query_matches_secret_id_substring_case_insensitively(self):
        self.write("db_password", "20261001_040000_000000")
        self.write("stripe_key", "20261002_040000_000000")
        result = self.manager.list_backup_page(query="  DB_pass ")
        self.assertEqual([b["secret_id"] for b in result["backups"]], ["db_password"])
        self.assertEqual(result["total"], 1)

    def test_query_does_not_match_the_timestamp_or_extension(self):
        self.write("db", "20261001_040000_000000")
        self.assertEqual(self.manager.list_backup_page(query="2026")["total"], 0)
        self.assertEqual(self.manager.list_backup_page(query="json")["total"], 0)

    def test_query_wildcards_are_literal(self):
        self.write("db", "20261001_040000_000000")
        self.assertEqual(self.manager.list_backup_page(query="*")["total"], 0)
        self.assertEqual(self.manager.list_backup_page(query="?")["total"], 0)

    def test_returns_file_names_not_paths_and_no_secret_values(self):
        self.write("db", "20261001_040000_000000")
        result = self.manager.list_backup_page()
        item = result["backups"][0]
        self.assertNotIn("/", item["file"])
        self.assertEqual(set(item), {"file", "secret_id", "created", "encrypted", "readable"})
        dumped = json.dumps(result)
        self.assertNotIn("SECRET-VALUE", dumped)
        self.assertNotIn(str(self.dir), dumped)

    def test_reads_encrypted_flag(self):
        self.write("db", "20261001_040000_000000", encrypted=True)
        self.write("api", "20261002_040000_000000", encrypted=False)
        flags = {b["secret_id"]: b["encrypted"] for b in self.manager.list_backup_page()["backups"]}
        self.assertEqual(flags, {"db": True, "api": False})

    def test_created_is_converted_to_utc(self):
        self.write("db", "20261001_040000_000000", created="2026-10-12T04:00:00")
        created = self.manager.list_backup_page()["backups"][0]["created"]
        expected = datetime.fromisoformat("2026-10-12T04:00:00").astimezone(timezone.utc)
        self.assertEqual(created, expected.isoformat())
        self.assertTrue(created.endswith("+00:00"))

    def test_unreadable_file_is_listed_but_flagged(self):
        (self.dir / "db_20261001_040000_000000.json").write_text("{not json")
        item = self.manager.list_backup_page()["backups"][0]
        self.assertFalse(item["readable"])
        self.assertIsNone(item["encrypted"])
        self.assertIsNone(item["created"])
        self.assertEqual(item["secret_id"], "db")

    def test_other_files_are_ignored(self):
        self.write("db", "20261001_040000_000000")
        (self.dir / "README.txt").write_text("hi")
        (self.dir / "db_20261001_040000_000000.json.sha256").write_text("abc")
        self.assertEqual(self.manager.list_backup_page()["total"], 1)

    def test_only_the_requested_page_is_opened(self):
        for day in range(1, 21):
            self.write("db", f"202610{day:02d}_040000_000000")
        with patch("secret_rotator.backup_manager.json.load", wraps=json.load) as spy:
            self.manager.list_backup_page(page=2, per_page=5)
        self.assertEqual(spy.call_count, 5)

    def test_real_encrypted_backup_exposes_no_values(self):
        manager = BackupManager(backup_dir=str(self.dir / "enc"), encrypt_backups=True)
        manager.create_backup("real", "PLAINTEXT-OLD", "PLAINTEXT-NEW")
        result = manager.list_backup_page()
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["backups"][0]["encrypted"])
        dumped = json.dumps(result)
        self.assertNotIn("PLAINTEXT", dumped)

    def test_agrees_with_count_backups(self):
        for day in range(1, 6):
            self.write("db", f"202610{day:02d}_040000_000000")
        self.assertEqual(self.manager.list_backup_page()["total"], self.manager.count_backups())


class TestTimeHelpers(unittest.TestCase):
    def test_aware_timestamp_is_converted_to_utc(self):
        parsed = parse_time("2026-10-13T06:00:00+02:00")
        self.assertEqual(parsed, datetime(2026, 10, 13, 4, 0, tzinfo=timezone.utc))

    def test_naive_timestamp_is_treated_as_local_time(self):
        naive = "2026-10-13T06:00:00"
        expected = datetime.fromisoformat(naive).astimezone(timezone.utc)
        self.assertEqual(parse_time(naive), expected)

    def test_bad_input_gives_none(self):
        for value in (None, "", "yesterday", 12345):
            self.assertIsNone(parse_time(value))
            self.assertIsNone(to_utc_iso(value))

    def test_to_utc_iso(self):
        self.assertEqual(to_utc_iso("2026-10-13T06:00:00+02:00"), "2026-10-13T04:00:00+00:00")


class TestBackupListApi(ListingFixture):
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
        engine = RotationEngine()
        engine.backup_manager = self.manager
        app = create_app(engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()
        limiter.reset()
        for day in range(1, 8):
            self.write("db_password" if day % 2 else "stripe_key", f"202610{day:02d}_040000_000000")

    def login(self):
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def test_requires_login(self):
        self.assertEqual(self.client.get("/api/backup-list").status_code, 401)

    def test_default_page(self):
        self.login()
        data = self.client.get("/api/backup-list").get_json()
        self.assertEqual((data["total"], data["page"], data["per_page"]), (7, 1, 25))
        self.assertEqual(len(data["backups"]), 7)

    def test_filter_and_paging_parameters(self):
        self.login()
        data = self.client.get("/api/backup-list?q=db_pass&per_page=2&page=2").get_json()
        self.assertEqual(data["total"], 4)
        self.assertEqual((data["page"], data["pages"]), (2, 2))
        self.assertTrue(all(b["secret_id"] == "db_password" for b in data["backups"]))

    def test_non_numeric_paging_is_a_400(self):
        self.login()
        self.assertEqual(self.client.get("/api/backup-list?page=abc").status_code, 400)
        self.assertEqual(self.client.get("/api/backup-list?per_page=x").status_code, 400)

    def test_response_has_no_secret_values_or_paths(self):
        self.login()
        body = self.client.get("/api/backup-list").get_data(as_text=True)
        self.assertNotIn("SECRET-VALUE", body)
        self.assertNotIn(str(self.dir), body)

    def test_existing_backups_endpoint_is_unchanged(self):
        self.login()
        data = self.client.get("/api/backups").get_json()
        self.assertEqual(len(data["backups"]), 7)
        self.assertIn("backup_file", data["backups"][0])


if __name__ == "__main__":
    unittest.main()

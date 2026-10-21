"""Tests for the settings catalog, SettingsAdminService, the batch store
change, the live default schedule and the /api/settings endpoints."""

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

from secret_rotator.activity import ActivityService
from secret_rotator.audit_log import AuditLog
from secret_rotator.config.overrides import (
    SECRET_SETTING_KEYS,
    ConflictError,
    OverridesError,
    OverridesStore,
)
from secret_rotator.config.settings import Settings
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.scheduler import ROTATION_TAG, RotationScheduler
from secret_rotator.settings_admin import SettingsAdminService, SettingsValidationError
from secret_rotator.settings_catalog import (
    BY_KEY,
    SECTIONS,
    SETTINGS,
    UNAVAILABLE,
    validate_value,
)
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

SRC = Path(__file__).parent.parent / "src" / "secret_rotator"
SECRET = "AKIA-SUPER-SECRET-VALUE"


class TestCatalog(unittest.TestCase):
    def test_keys_are_unique_and_sections_exist(self):
        self.assertEqual(len({s.key for s in SETTINGS}), len(SETTINGS))
        sections = {key for key, _, _ in SECTIONS}
        self.assertTrue(all(s.section in sections for s in SETTINGS))
        self.assertTrue(all(sec in sections for sec, _, _ in UNAVAILABLE))

    def test_every_listed_setting_is_actually_read_by_the_program(self):
        """The catalog must never offer a control the code ignores."""
        skip = {"settings_catalog.py", "settings_admin.py", "overrides.py"}
        text = "\n".join(
            p.read_text()
            for p in SRC.rglob("*.py")
            if p.name not in skip and "web_interface" not in p.name
        )
        missing = [s.key for s in SETTINGS if f'"{s.key}"' not in text and f"'{s.key}'" not in text]
        self.assertEqual(missing, [])

    def test_the_settings_that_take_effect_without_a_restart(self):
        live = {s.key for s in SETTINGS if not s.restart}
        self.assertEqual(
            live,
            {
                "rotation.schedule",
                "rotation.backup_old_secrets",
                "backup.retention.days",
                "distributed.master_key_lock_timeout",
                "distributed.job_timeout",
                "logging.level",
            },
        )

    def test_read_only_settings_explain_why(self):
        read_only = {s.key for s in SETTINGS if not s.editable}
        self.assertEqual(
            read_only,
            {
                "security.encryption.enabled",
                "security.encryption.master_key_file",
                "backup.storage_path",
                "logging.file",
            },
        )
        self.assertTrue(all(BY_KEY[k].reason for k in read_only))

    def test_nothing_that_could_take_over_the_dashboard_is_offered(self):
        for key in (
            "web.secret_key",
            "web.auth.username",
            "web.auth.password_hash",
            "web.enabled",
            "audit.log_file",
        ):
            self.assertNotIn(key, BY_KEY)

    def test_unimplemented_controls_are_not_offered_as_settings(self):
        for key in (
            "rotation.timeout",
            "rotation.retry_attempts",
            "backup.enabled",
            "backup.compression",
            "notifications.enabled",
            "logging.mask_sensitive",
        ):
            self.assertNotIn(key, BY_KEY)
        labels = " ".join(label for _, label, _ in UNAVAILABLE)
        for word in ("Retry attempts", "Timeout", "Email and Slack alerts", "Mask sensitive data"):
            self.assertIn(word, labels)

    def test_secrets_match_the_encrypted_storage_list(self):
        self.assertEqual({s.key for s in SETTINGS if s.secret}, SECRET_SETTING_KEYS)
        for key in SECRET_SETTING_KEYS:
            self.assertEqual(BY_KEY[key].kind, "secret")
            self.assertIsNone(BY_KEY[key].describe()["default"])

    def test_defaults_pass_their_own_validation(self):
        for setting in SETTINGS:
            if setting.editable and setting.default is not None and not setting.secret:
                value, problem = validate_value(setting, setting.default)
                self.assertIsNone(problem, f"{setting.key}: {problem}")

    def test_describe_is_json_serialisable(self):
        json.dumps([s.describe() for s in SETTINGS])


class TestValidateValue(unittest.TestCase):
    def check(self, key, value):
        return validate_value(BY_KEY[key], value)

    def good(self, key, *values):
        for value in values:
            canonical, problem = self.check(key, value)
            self.assertIsNone(problem, f"{key}={value!r}: {problem}")

    def bad(self, key, *values):
        for value in values:
            self.assertIsNotNone(self.check(key, value)[1], f"{key}={value!r} should be refused")

    def test_booleans(self):
        self.good("rotation.backup_old_secrets", True, False)
        self.bad("rotation.backup_old_secrets", "true", 1, 0, None)

    def test_integers(self):
        self.good("web.port", 1, 8080, 65535)
        self.bad("web.port", 0, 65536, "8080", 80.5, True, None)
        self.good("logging.backup_count", 0, 100)
        self.bad("logging.backup_count", -1, 101)

    def test_choices(self):
        self.good("logging.level", "DEBUG", "CRITICAL")
        self.bad("logging.level", "debug", "TRACE", 5)

    def test_times(self):
        self.good("backup.cleanup_time", "00:00", "03:00", "23:59")
        self.bad("backup.cleanup_time", "24:00", "3:00", "03:60", "03:00:00", "noon", 300)

    def test_schedule_is_canonicalised(self):
        self.assertEqual(self.check("rotation.schedule", " Every_1_Hour "), ("every_1_hours", None))
        self.good("rotation.schedule", "daily", "monthly", "every_30_minutes")
        self.bad("rotation.schedule", "hourly", "", "every_0_minutes", 5)

    def test_sizes(self):
        self.good("logging.max_file_size", "10MB", "512KB", "2GB")
        self.bad("logging.max_file_size", "10", "0MB", "10mb", "10 MB", "99999MB", "-5MB")

    def test_hosts(self):
        self.good(
            "web.host",
            "localhost",
            "0.0.0.0",
            "127.0.0.1",
            "::1",
            "dashboard.example.com",
            "host-1",
        )
        self.bad("web.host", "", "a b", "-bad.example", "bad..example", "http://x", "x/y", "a;b")

    def test_endpoint_url(self):
        self.good(
            "backup.remote_backup.endpoint_url",
            "https://acct.r2.cloudflarestorage.com",
            "http://minio:9000",
            "",
        )
        self.bad(
            "backup.remote_backup.endpoint_url",
            "ftp://x",
            "minio:9000",
            "https://",
            "https://user:pw@host",
            "https://host name",
            "javascript:alert(1)",
        )

    def test_bucket_region_prefix_queue(self):
        self.good("backup.remote_backup.bucket", "my-secret-backups", "a.b-c")
        self.bad(
            "backup.remote_backup.bucket", "UPPER", "ab", "-start", "end-", "has space", "x" * 64
        )
        self.good("backup.remote_backup.region", "us-east-1", "auto")
        self.bad("backup.remote_backup.region", "US_EAST", "", "x" * 33)
        self.good("backup.remote_backup.prefix", "", "rotator/backups/", "a_b-c.d")
        self.bad("backup.remote_backup.prefix", "/abs", "../up", "a/../b", "has space", "x" * 201)
        self.good("distributed.queue_name", "secret-rotator-rotations", "q:1.a_b")
        self.bad("distributed.queue_name", "", "has space", "x" * 65, "q/1")

    def test_secrets_must_be_text(self):
        self.good("backup.remote_backup.secret_access_key", "AKIA123")
        self.bad("backup.remote_backup.secret_access_key", "", 5, None, "x" * 5000, "a\nb")

    def test_redis_url(self):
        self.good(
            "distributed.redis_url",
            "redis://localhost:6379/0",
            "redis://:pw@cache:6379/0",
            "rediss://u:p@h/1",
            "unix:///tmp/redis.sock",
        )
        self.bad("distributed.redis_url", "http://x", "localhost:6379", "redis://", "redis://h ost")

    def test_control_characters_are_refused_everywhere(self):
        self.bad("backup.remote_backup.bucket", "a\x00bc")
        self.bad("web.host", "a\nb")

    def test_wrong_types_are_refused_not_coerced(self):
        for key in (
            "backup.cleanup_time",
            "web.host",
            "backup.remote_backup.bucket",
            "logging.max_file_size",
        ):
            self.bad(key, 5, ["x"], {"a": 1}, None, True)


class Fixture(unittest.TestCase):
    def setUp(self):
        DistributedLock.reset_local_locks()
        schedule.clear()
        self.addCleanup(schedule.clear)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.config = {
            "rotation": {"schedule": "weekly"},
            "backup": {"remote_backup": {"enabled": False, "bucket": "from-config"}},
            "jobs": [{"name": "a", "provider": "p", "rotator": "r", "secret_id": "a"}],
        }
        self.yaml_path = self.dir / "config.yaml"
        self.yaml_path.write_text(yaml.safe_dump(self.config))
        self.yaml_before = self.yaml_path.read_text()
        self.overrides_path = self.dir / "dash.json"
        env = patch.dict(
            os.environ, {"SECRET_ROTATOR_DASHBOARD_SETTINGS": str(self.overrides_path)}
        )
        env.start()
        self.addCleanup(env.stop)
        self.settings = Settings(config_path=str(self.yaml_path))
        self.audit = AuditLog(audit_file=str(self.dir / "audit.log"))

        self.engine = RotationEngine()
        self.engine.backup_manager = MagicMock()
        self.engine.replace_rotation_jobs(self.settings.get("jobs"))
        self.scheduler = RotationScheduler(
            rotation_function=self.engine.rotate_all_secrets,
            backup_manager=MagicMock(),
            rotate_jobs_function=self.engine.rotate_jobs,
            jobs_provider=lambda: self.engine.rotation_jobs,
        )
        self.engine.scheduler = self.scheduler
        self.scheduler.setup_schedule("weekly")
        self.admin = SettingsAdminService(self.engine, self.settings, audit=self.audit)

    def raw(self):
        return self.overrides_path.read_text()

    def every_file_text(self):
        return "\n".join(
            p.read_text(errors="replace")
            for p in self.dir.rglob("*")
            if p.is_file() and p.suffix != ".key"
        )

    def find(self, listing, key):
        for section in listing["sections"]:
            for item in section["settings"]:
                if item["key"] == key:
                    return item
        raise KeyError(key)

    def update(self, changes=None, reset=None, **kwargs):
        return self.admin.update(changes or {}, reset or [], "alice", **kwargs)


class TestListing(Fixture):
    def test_sections_follow_the_design(self):
        listing = self.admin.listing()
        self.assertEqual(
            [s["label"] for s in listing["sections"]],
            [
                "Rotation",
                "Security & encryption",
                "Backups & retention",
                "Remote backup",
                "Distributed mode",
                "Web & access",
                "Logging",
                "Notifications",
            ],
        )
        self.assertEqual(listing["revision"], 0)
        self.assertEqual(listing["pending_restart"], [])

    def test_values_and_sources(self):
        listing = self.admin.listing()
        self.assertEqual(self.find(listing, "rotation.schedule")["source"], "config")
        self.assertEqual(self.find(listing, "rotation.schedule")["value"], "weekly")
        self.assertEqual(self.find(listing, "web.port")["source"], "default")
        self.assertEqual(self.find(listing, "web.port")["value"], 8080)
        self.assertEqual(self.find(listing, "backup.remote_backup.bucket")["value"], "from-config")
        self.update({"web.port": 9090})
        port = self.find(self.admin.listing(), "web.port")
        self.assertEqual((port["source"], port["value"]), ("dashboard", 9090))

    def test_each_setting_says_whether_it_needs_a_restart(self):
        listing = self.admin.listing()
        self.assertFalse(self.find(listing, "rotation.schedule")["restart"])
        self.assertTrue(self.find(listing, "web.port")["restart"])

    def test_read_only_settings_are_listed_with_their_reason(self):
        item = self.find(self.admin.listing(), "security.encryption.master_key_file")
        self.assertFalse(item["editable"])
        self.assertIn("config.yaml", item["reason"])

    def test_unavailable_controls_are_listed_per_section(self):
        sections = {s["key"]: s for s in self.admin.listing()["sections"]}
        self.assertEqual(sections["notifications"]["settings"], [])
        self.assertIn(
            "Nothing in this version sends notifications",
            sections["notifications"]["unavailable"][0]["reason"],
        )
        self.assertTrue(
            any(u["label"] == "Retry attempts" for u in sections["rotation"]["unavailable"])
        )

    def test_credentials_are_write_only(self):
        self.update(
            {
                "backup.remote_backup.access_key_id": "AKIA1",
                "backup.remote_backup.secret_access_key": SECRET,
            }
        )
        listing = self.admin.listing()
        item = self.find(listing, "backup.remote_backup.secret_access_key")
        self.assertTrue(item["is_set"])
        self.assertIsNone(item["value"])
        self.assertEqual(item["source"], "dashboard")
        self.assertNotIn(SECRET, json.dumps(listing))
        self.assertFalse(self.find(listing, "distributed.redis_url")["is_set"])

    def test_a_credential_in_config_yaml_shows_as_set_without_its_value(self):
        self.config["distributed"] = {"redis_url": "redis://:from-config-pw@cache/0"}
        self.yaml_path.write_text(yaml.safe_dump(self.config))
        self.settings.reload()
        item = self.find(self.admin.listing(), "distributed.redis_url")
        self.assertEqual((item["is_set"], item["source"], item["value"]), (True, "config", None))
        self.assertNotIn("from-config-pw", json.dumps(self.admin.listing()))

    def test_file_warnings_are_passed_on(self):
        self.update({"web.port": 9090})
        data = json.loads(self.raw())
        data["settings"]["backup.remote_backup.secret_access_key"] = "plain"
        self.overrides_path.write_text(json.dumps(data))
        listing = self.admin.listing()
        self.assertTrue(any("plain text" in w for w in listing["warnings"]))
        self.assertNotIn('plain"', json.dumps(listing["warnings"]))


class TestUpdate(Fixture):
    def test_saves_in_the_dashboard_file_never_in_config_yaml(self):
        result = self.update({"web.port": 9090, "backup.retention.days": 30})
        self.assertEqual(result["saved"], ["backup.retention.days", "web.port"])
        self.assertEqual(self.yaml_path.read_text(), self.yaml_before)
        self.assertEqual(
            json.loads(self.raw())["settings"], {"web.port": 9090, "backup.retention.days": 30}
        )
        self.assertEqual(self.settings.get("web.port"), 9090)

    def test_a_whole_form_is_one_write(self):
        self.update({"web.port": 9090, "web.threads": 4, "logging.level": "DEBUG"})
        self.assertEqual(self.admin.revision, 1)

    def test_all_or_nothing(self):
        with self.assertRaises(SettingsValidationError) as caught:
            self.update({"web.port": 9090, "web.threads": 0, "logging.level": "LOUD"})
        self.assertEqual(set(caught.exception.fields), {"web.threads", "logging.level"})
        self.assertFalse(self.overrides_path.exists())
        self.assertIsNone(self.settings.get("web.port"))  # not in config.yaml: untouched

    def test_values_are_canonicalised(self):
        self.update({"rotation.schedule": " Every_1_Hour "})
        self.assertEqual(self.settings.get("rotation.schedule"), "every_1_hours")

    def test_unknown_and_read_only_settings_are_refused(self):
        with self.assertRaises(SettingsValidationError) as caught:
            self.update(
                {
                    "web.secret_key": "x",
                    "security.encryption.master_key_file": "/tmp/evil.key",
                    "web.auth.username": "root",
                }
            )
        fields = caught.exception.fields
        self.assertEqual(fields["web.secret_key"], "unknown setting")
        self.assertIn("cannot be changed here", fields["security.encryption.master_key_file"])
        self.assertEqual(fields["web.auth.username"], "unknown setting")
        self.assertFalse(self.overrides_path.exists())

    def test_read_only_settings_cannot_be_reset_either(self):
        with self.assertRaises(SettingsValidationError):
            self.update(reset=["logging.file"])

    def test_bad_shapes(self):
        for changes, reset in (([], []), ("x", []), ({}, "x"), ({}, {"a": 1}), (None, [])):
            with self.assertRaises(SettingsValidationError):
                self.admin.update(changes, reset, "alice")
        with self.assertRaises(SettingsValidationError) as caught:
            self.admin.update({}, [5], "alice")
        self.assertIn("5", caught.exception.fields)

    def test_changing_and_resetting_the_same_key_is_refused(self):
        with self.assertRaises(SettingsValidationError) as caught:
            self.update({"web.port": 9090}, reset=["web.port"])
        self.assertIn("both changed and reset", caught.exception.fields["web.port"])

    def test_reset_restores_config_yaml_then_the_default(self):
        self.update({"rotation.schedule": "daily", "web.port": 9090})
        result = self.update(reset=["rotation.schedule", "web.port"])
        self.assertEqual(result["reset"], ["rotation.schedule", "web.port"])
        self.assertEqual(self.settings.get("rotation.schedule"), "weekly")  # config.yaml's value
        self.assertEqual(
            self.settings.get("web.port"), None
        )  # no default in the file: the code's default applies

    def test_changes_that_change_nothing_are_not_saved(self):
        self.assertEqual(
            self.update({"rotation.schedule": "weekly"})["saved"], []
        )  # equals config.yaml
        self.assertEqual(self.update(reset=["web.port"])["reset"], [])  # nothing to reset
        self.assertFalse(self.overrides_path.exists())
        self.assertEqual(self.admin.revision, 0)

    def test_audit_lists_key_names_never_values(self):
        self.update(
            {
                "web.port": 9090,
                "backup.remote_backup.access_key_id": "AKIA1",
                "backup.remote_backup.secret_access_key": SECRET,
            }
        )
        event = [e for e in self.audit.read_events() if e["action"] == "settings_update"][0]
        self.assertEqual(event["actor"], "alice")
        self.assertEqual(
            event["details"],
            {
                "changed": [
                    "backup.remote_backup.access_key_id",
                    "backup.remote_backup.secret_access_key",
                    "web.port",
                ],
                "reset": [],
            },
        )
        self.assertNotIn(SECRET, json.dumps(event))
        self.assertNotIn("9090", json.dumps(event))

    def test_revision_conflict(self):
        self.update({"web.port": 9090})
        with self.assertRaises(ConflictError):
            self.update({"web.threads": 4}, revision=0)
        self.assertNotEqual(self.settings.get("web.threads"), 4)
        self.update({"web.threads": 4}, revision=1)

    def test_activity_message(self):
        self.update(
            {
                "web.port": 9090,
                "web.threads": 4,
                "logging.level": "DEBUG",
                "backup.retention.days": 30,
            }
        )
        message = ActivityService(audit=self.audit).query()["events"][0]["message"]
        self.assertEqual(
            message,
            "alice changed settings: backup.retention.days, logging.level, web.port and 1 more",
        )


class TestCredentials(Fixture):
    def test_stored_encrypted_and_visible_to_the_program(self):
        self.update(
            {
                "backup.remote_backup.access_key_id": "AKIA1",
                "backup.remote_backup.secret_access_key": SECRET,
            }
        )
        self.assertNotIn(SECRET, self.every_file_text())
        self.assertEqual(self.settings.get("backup.remote_backup.secret_access_key"), SECRET)
        self.assertNotIn(SECRET, self.yaml_path.read_text())

    def test_the_two_keys_go_together(self):
        with self.assertRaises(SettingsValidationError) as caught:
            self.update({"backup.remote_backup.access_key_id": "AKIA1"})
        self.assertIn("backup.remote_backup.secret_access_key", caught.exception.fields)
        with self.assertRaises(SettingsValidationError):
            self.update({"backup.remote_backup.secret_access_key": SECRET})
        self.update(
            {
                "backup.remote_backup.access_key_id": "AKIA1",
                "backup.remote_backup.secret_access_key": SECRET,
            }
        )
        with self.assertRaises(SettingsValidationError):
            self.update(reset=["backup.remote_backup.access_key_id"])
        self.update(
            reset=["backup.remote_backup.access_key_id", "backup.remote_backup.secret_access_key"]
        )

    def test_reset_clears_them(self):
        self.update(
            {
                "backup.remote_backup.access_key_id": "AKIA1",
                "backup.remote_backup.secret_access_key": SECRET,
            }
        )
        self.update(
            reset=["backup.remote_backup.access_key_id", "backup.remote_backup.secret_access_key"]
        )
        self.assertIsNone(self.settings.get("backup.remote_backup.secret_access_key"))
        self.assertFalse(
            self.find(self.admin.listing(), "backup.remote_backup.secret_access_key")["is_set"]
        )

    def test_empty_and_invalid_credentials_are_refused(self):
        for value in ("", None, 5):
            with self.assertRaises(SettingsValidationError):
                self.update({"distributed.redis_url": value})
        with self.assertRaises(SettingsValidationError) as caught:
            self.update({"distributed.redis_url": "http://not-redis"})
        self.assertIn("redis://", caught.exception.fields["distributed.redis_url"])

    def test_the_response_never_contains_a_credential(self):
        self.update(
            {
                "backup.remote_backup.access_key_id": "AKIA1",
                "backup.remote_backup.secret_access_key": SECRET,
            }
        )
        self.assertNotIn(SECRET, json.dumps(self.admin.listing()))

    def test_a_credential_is_marked_pending_without_exposing_it(self):
        self.update({"distributed.redis_url": "redis://:pw@cache/0", "distributed.enabled": True})
        self.assertIn("distributed.redis_url", self.admin.pending_restart())
        self.assertNotIn("pw@cache", json.dumps(self.admin.listing()))


class TestCrossChecks(Fixture):
    def test_remote_backup_needs_a_bucket(self):
        self.config["backup"]["remote_backup"].pop("bucket")
        self.yaml_path.write_text(yaml.safe_dump(self.config))
        self.settings.reload()
        with self.assertRaises(SettingsValidationError) as caught:
            self.update({"backup.remote_backup.enabled": True})
        self.assertIn("required", caught.exception.fields["backup.remote_backup.bucket"])
        self.update(
            {"backup.remote_backup.enabled": True, "backup.remote_backup.bucket": "my-bucket"}
        )

    def test_a_bucket_in_config_yaml_is_enough(self):
        self.update({"backup.remote_backup.enabled": True})  # config.yaml has a bucket

    def test_resetting_the_bucket_is_checked_against_what_would_remain(self):
        self.config["backup"]["remote_backup"].pop("bucket")
        self.yaml_path.write_text(yaml.safe_dump(self.config))
        self.settings.reload()
        self.update({"backup.remote_backup.enabled": True, "backup.remote_backup.bucket": "mine"})
        with self.assertRaises(SettingsValidationError):
            self.update(reset=["backup.remote_backup.bucket"])  # nothing would remain

    def test_distributed_mode_needs_a_redis_url(self):
        with patch.dict(os.environ):
            os.environ.pop("REDIS_URL", None)
            with self.assertRaises(SettingsValidationError) as caught:
                self.update({"distributed.enabled": True})
            self.assertIn("REDIS_URL", caught.exception.fields["distributed.redis_url"])
            self.update(
                {"distributed.enabled": True, "distributed.redis_url": "redis://localhost:6379/0"}
            )

    def test_the_redis_url_environment_variable_counts(self):
        with patch.dict(os.environ, {"REDIS_URL": "redis://env:6379/0"}):
            self.update({"distributed.enabled": True})


class TestPendingRestart(Fixture):
    def test_a_restart_setting_is_pending_until_changed_back(self):
        self.update({"web.port": 9090})
        self.assertEqual(self.admin.pending_restart(), ["web.port"])
        self.assertTrue(self.find(self.admin.listing(), "web.port")["restart_pending"])
        self.update({"web.port": 8080})
        self.assertEqual(self.admin.pending_restart(), [])

    def test_resetting_clears_it(self):
        self.update({"web.port": 9090})
        self.update(reset=["web.port"])
        self.assertEqual(self.admin.pending_restart(), [])

    def test_live_settings_are_never_pending(self):
        self.update(
            {
                "backup.retention.days": 30,
                "rotation.backup_old_secrets": False,
                "distributed.job_timeout": 120,
            }
        )
        self.assertEqual(self.admin.pending_restart(), [])

    def test_the_response_lists_what_needs_a_restart(self):
        result = self.update({"web.port": 9090, "backup.retention.days": 30})
        self.assertEqual(result["restart_required"], ["web.port"])

    def test_a_restarted_process_starts_clean(self):
        self.update({"web.port": 9090})
        restarted = SettingsAdminService(
            self.engine, Settings(config_path=str(self.yaml_path)), audit=self.audit
        )
        self.assertEqual(restarted.pending_restart(), [])


class TestAppliedLive(Fixture):
    def entry_names(self):
        return {
            tag.split(":", 1)[1]
            for e in schedule.get_jobs(ROTATION_TAG)
            for tag in e.tags
            if tag.startswith(ROTATION_TAG + ":")
        }

    def test_the_default_schedule_is_applied_to_the_running_scheduler(self):
        self.assertEqual(self.entry_names(), {"weekly"})
        self.update({"rotation.schedule": "every_2_hours"})
        self.assertEqual(self.scheduler.default_schedule, "every_2_hours")
        self.assertEqual(self.entry_names(), {"every_2_hours"})

    def test_resetting_the_schedule_is_applied_too(self):
        self.update({"rotation.schedule": "daily"})
        self.update(reset=["rotation.schedule"])
        self.assertEqual(self.entry_names(), {"weekly"})

    def test_an_invalid_schedule_is_refused_before_anything_changes(self):
        with self.assertRaises(SettingsValidationError):
            self.update({"rotation.schedule": "hourly"})
        self.assertEqual(self.entry_names(), {"weekly"})

    def test_the_log_level_is_applied_live(self):
        with patch("secret_rotator.utils.logger.set_log_level") as setter:
            self.update({"logging.level": "DEBUG"})
        setter.assert_called_once_with("DEBUG")

    def test_a_failing_live_apply_does_not_lose_the_save(self):
        self.scheduler.set_default_schedule = MagicMock(side_effect=RuntimeError("boom"))
        result = self.update({"rotation.schedule": "daily"})
        self.assertEqual(result["saved"], ["rotation.schedule"])
        self.assertEqual(self.settings.get("rotation.schedule"), "daily")

    def test_settings_without_a_hook_do_not_touch_the_scheduler(self):
        self.scheduler.set_default_schedule = MagicMock()
        self.update({"web.port": 9090})
        self.scheduler.set_default_schedule.assert_not_called()


class TestSchedulerDefault(unittest.TestCase):
    def setUp(self):
        schedule.clear()
        self.addCleanup(schedule.clear)
        self.jobs = [
            {"name": "a", "provider": "p", "rotator": "r", "secret_id": "a"},
            {"name": "b", "provider": "p", "rotator": "r", "secret_id": "b", "schedule": "monthly"},
        ]
        self.scheduler = RotationScheduler(
            rotation_function=lambda: {},
            backup_manager=MagicMock(),
            rotate_jobs_function=lambda names: {},
            jobs_provider=lambda: self.jobs,
        )
        self.scheduler.setup_schedule("daily")

    def names(self):
        return {
            tag.split(":", 1)[1]
            for e in schedule.get_jobs(ROTATION_TAG)
            for tag in e.tags
            if tag.startswith(ROTATION_TAG + ":")
        }

    def test_jobs_following_the_default_move_and_others_stay(self):
        self.assertEqual(self.names(), {"daily", "monthly"})
        self.scheduler.set_default_schedule("weekly")
        self.assertEqual(self.names(), {"weekly", "monthly"})
        self.assertEqual(self.scheduler.effective_schedule(self.jobs[0]), "weekly")
        self.assertEqual(self.scheduler.effective_schedule(self.jobs[1]), "monthly")

    def test_maintenance_schedules_are_untouched(self):
        before = len(schedule.get_jobs()) - len(schedule.get_jobs(ROTATION_TAG))
        self.scheduler.set_default_schedule("weekly")
        self.assertEqual(len(schedule.get_jobs()) - len(schedule.get_jobs(ROTATION_TAG)), before)

    def test_an_invalid_schedule_changes_nothing(self):
        with self.assertRaises(ValueError):
            self.scheduler.set_default_schedule("hourly")
        self.assertEqual(self.scheduler.default_schedule, "daily")
        self.assertEqual(self.names(), {"daily", "monthly"})

    def test_next_rotation_follows_the_new_default(self):
        before = self.scheduler.next_rotation_for("daily")
        self.scheduler.set_default_schedule("every_2_hours")
        self.assertIsNone(self.scheduler.next_rotation_for("daily"))
        self.assertIsNotNone(self.scheduler.next_rotation_for("every_2_hours"))
        self.assertIsNotNone(before)


class TestBatchStoreChange(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "dash.json"
        self.store = OverridesStore(self.path, base_provider=lambda: {})

    def test_sets_and_resets_in_one_revision(self):
        self.store.set_setting("a.b", 1, "alice")
        revision = self.store.change_settings({"c.d": 2, "e.f": 3}, ["a.b"], "alice")
        self.assertEqual(revision, 2)
        self.assertEqual(json.loads(self.path.read_text())["settings"], {"c.d": 2, "e.f": 3})

    def test_secrets_in_a_batch_are_encrypted(self):
        self.store.change_settings(
            {"distributed.redis_url": "redis://:pw@h/0", "a.b": 1}, [], "alice"
        )
        self.assertNotIn("pw@h", self.path.read_text())

    def test_nothing_is_written_if_any_part_is_invalid(self):
        for bad in ({"bad key": 1}, {"a.b": object()}, {"distributed.redis_url": ""}):
            with self.assertRaises(OverridesError):
                self.store.change_settings(bad, [], "alice")
        with self.assertRaises(OverridesError):
            self.store.change_settings({"a.b": 1}, ["bad key"], "alice")
        self.assertFalse(self.path.exists())

    def test_expected_revision(self):
        self.store.set_setting("a.b", 1, "alice")
        with self.assertRaises(ConflictError):
            self.store.change_settings({"a.c": 1}, [], "alice", expected_revision=0)


USERNAME = "settings-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)


class TestSettingsApi(Fixture):
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
        app = create_app(self.engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.settings_admin = self.admin
        self.client = app.test_client()
        limiter.reset()

    def login(self):
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def put(self, **body):
        return self.client.put("/api/settings", json=body)

    def test_requires_login(self):
        self.assertEqual(self.client.get("/api/settings").status_code, 401)
        self.assertEqual(
            self.client.put("/api/settings", json={"changes": {"web.port": 9}}).status_code, 401
        )
        self.assertFalse(self.overrides_path.exists())

    def test_get(self):
        self.login()
        data = self.client.get("/api/settings").get_json()
        self.assertEqual(len(data["sections"]), 8)
        self.assertEqual(data["revision"], 0)

    def test_put_saves_and_returns_the_refreshed_listing(self):
        self.login()
        resp = self.put(changes={"web.port": 9090, "backup.retention.days": 30}, revision=0)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["saved"], ["backup.retention.days", "web.port"])
        self.assertEqual(data["restart_required"], ["web.port"])
        self.assertEqual(data["revision"], 1)
        self.assertEqual(self.find(data["settings"], "web.port")["value"], 9090)
        self.assertEqual(
            [e["actor"] for e in self.audit.read_events() if e["action"] == "settings_update"],
            [USERNAME],
        )

    def test_validation_errors_are_400_with_a_message_per_setting(self):
        self.login()
        resp = self.put(
            changes={"web.port": 0, "web.secret_key": "x", "logging.file": "/etc/passwd"}
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data["error"], "Invalid settings")
        self.assertEqual(set(data["fields"]), {"web.port", "web.secret_key", "logging.file"})
        self.assertFalse(self.overrides_path.exists())

    def test_stale_revision_is_409(self):
        self.login()
        self.put(changes={"web.port": 9090})
        resp = self.put(changes={"web.threads": 4}, revision=0)
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(resp.get_json()["conflict"])

    def test_body_must_be_json(self):
        self.login()
        self.assertEqual(self.client.put("/api/settings", data="x=1").status_code, 400)
        self.assertEqual(self.client.put("/api/settings", json=[1]).status_code, 400)

    def test_missing_changes_is_a_no_op_not_an_error(self):
        self.login()
        resp = self.put()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["saved"], [])

    def test_responses_never_contain_credentials(self):
        self.login()
        resp = self.put(
            changes={
                "backup.remote_backup.access_key_id": "AKIA1",
                "backup.remote_backup.secret_access_key": SECRET,
            }
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(SECRET, resp.get_data(as_text=True))
        self.assertNotIn(SECRET, self.client.get("/api/settings").get_data(as_text=True))
        self.assertNotIn(SECRET, self.every_file_text())

    def test_put_is_rate_limited(self):
        self.login()
        codes = [self.put(changes={}).status_code for _ in range(31)]
        self.assertEqual(codes[-1], 429)

    def test_csrf_is_enforced(self):
        app = create_app(self.engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = True
        app.settings_admin = self.admin
        client = app.test_client()
        page = client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        client.post(
            "/login", data={"username": USERNAME, "password": PASSWORD, "csrf_token": token}
        )
        self.assertEqual(
            client.put("/api/settings", json={"changes": {"web.port": 9090}}).status_code, 400
        )
        self.assertFalse(self.overrides_path.exists())

    def test_existing_endpoints_are_unaffected(self):
        self.login()
        self.assertEqual(self.client.get("/api/jobs").status_code, 200)


if __name__ == "__main__":
    unittest.main()

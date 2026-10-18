"""Tests for dashboard job management: JobAdminService, pausing, and the
/api/jobs create/update/delete/pause/resume/reset endpoints."""

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
from secret_rotator.config.overrides import ConflictError
from secret_rotator.config.settings import Settings
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.job_admin import (
    JobAdminService,
    JobExistsError,
    JobNotFoundError,
    JobValidationError,
)
from secret_rotator.job_status import JobStatusService
from secret_rotator.providers.base import SecretProvider
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.rotators.base import SecretRotator
from secret_rotator.scheduler import ROTATION_TAG, RotationScheduler
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
        },
        {
            "name": "api",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "api_key",
        },
    ],
}


class FakeProvider(SecretProvider):
    def __init__(self, name):
        super().__init__(name=name, config={})
        self.store = {}

    def get_secret(self, secret_id):
        return "old"

    def update_secret(self, secret_id, new_value):
        self.store[secret_id] = new_value
        return True

    def validate_connection(self):
        return True


class FakeRotator(SecretRotator):
    def __init__(self, name):
        super().__init__(name=name, config={})

    def generate_new_secret(self):
        return "new"

    def validate_secret(self, value):
        return True


class Fixture(unittest.TestCase):
    def setUp(self):
        DistributedLock.reset_local_locks()
        schedule.clear()
        self.addCleanup(schedule.clear)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.yaml_path = self.dir / "config.yaml"
        self.yaml_path.write_text(yaml.safe_dump(BASE))
        self.yaml_before = self.yaml_path.read_text()
        self.overrides_path = self.dir / "dashboard-settings.json"
        env = patch.dict(
            os.environ, {"SECRET_ROTATOR_DASHBOARD_SETTINGS": str(self.overrides_path)}
        )
        env.start()
        self.addCleanup(env.stop)
        self.settings = Settings(config_path=str(self.yaml_path))

        self.audit = AuditLog(audit_file=str(self.dir / "audit.log"))
        for target, value in (
            ("secret_rotator.rotation_engine.audit_log", self.audit),
            ("secret_rotator.rotation_engine.time.sleep", lambda seconds: None),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.engine = RotationEngine()
        self.engine.backup_manager = MagicMock()
        self.engine.backup_manager.list_backup_page.return_value = {
            "backups": [],
            "total": 0,
            "page": 1,
            "per_page": 5,
            "pages": 1,
        }
        self.provider = FakeProvider("file_storage")
        self.engine.register_provider(self.provider)
        self.engine.register_provider(FakeProvider("aws"))
        self.engine.register_rotator(FakeRotator("password_gen"))
        self.engine.register_rotator(FakeRotator("api_key_gen"))
        self.engine.replace_rotation_jobs(self.settings.get("jobs"))

        self.scheduler = RotationScheduler(
            rotation_function=self.engine.rotate_all_secrets,
            backup_manager=MagicMock(),
            rotate_jobs_function=self.engine.rotate_jobs,
            jobs_provider=lambda: self.engine.rotation_jobs,
        )
        self.engine.scheduler = self.scheduler
        self.scheduler.setup_schedule("daily")

        self.admin = JobAdminService(self.engine, self.settings, audit=self.audit)

    def job(self, name):
        return next((j for j in self.engine.rotation_jobs if j["name"] == name), None)

    def names(self):
        return [j["name"] for j in self.engine.rotation_jobs]

    def entry_names(self):
        return {
            tag.split(":", 1)[1]
            for e in schedule.get_jobs(ROTATION_TAG)
            for tag in e.tags
            if tag.startswith(ROTATION_TAG + ":")
        }

    def on_disk(self):
        return json.loads(self.overrides_path.read_text())

    def audit_events(self, action=None):
        events = self.audit.read_events()
        return [e for e in events if action is None or e["action"] == action]

    def new_job(self, **overrides):
        job = {
            "name": "new",
            "provider": "aws",
            "rotator": "api_key_gen",
            "secret_id": "new_secret",
        }
        job.update(overrides)
        return job


class TestOptions(Fixture):
    def test_options(self):
        options = self.admin.options()
        self.assertEqual(options["providers"], ["aws", "file_storage"])
        self.assertEqual(options["rotators"], ["api_key_gen", "password_gen"])
        self.assertIn("monthly", options["schedules"])
        self.assertEqual(options["default_schedule"], "daily")
        self.assertEqual(options["revision"], 0)


class TestCreate(Fixture):
    def test_creates_and_applies_the_job_live(self):
        name = self.admin.create(self.new_job(schedule="every_2_hours", notification=True), "alice")
        self.assertEqual(name, "new")
        self.assertEqual(self.names(), ["db", "api", "new"])
        self.assertEqual(
            self.job("new"),
            {
                "name": "new",
                "provider": "aws",
                "rotator": "api_key_gen",
                "secret_id": "new_secret",
                "schedule": "every_2_hours",
                "notification": True,
            },
        )
        self.assertIn("every_2_hours", self.entry_names())  # scheduler refreshed

    def test_saved_in_the_dashboard_file_never_in_config_yaml(self):
        self.admin.create(self.new_job(), "alice")
        self.assertEqual(self.yaml_path.read_text(), self.yaml_before)
        self.assertIn("new", self.on_disk()["jobs"])
        self.assertEqual(self.on_disk()["updated_by"], "alice")

    def test_schedule_is_stored_canonically_and_blank_means_default(self):
        self.admin.create(self.new_job(name="a", secret_id="a", schedule=" Every_1_Hour "), "alice")
        self.admin.create(self.new_job(name="b", secret_id="b", schedule=""), "alice")
        self.assertEqual(self.job("a")["schedule"], "every_1_hours")
        self.assertNotIn("schedule", self.job("b"))

    def test_audit_event_names_the_actor_and_the_fields_only(self):
        self.admin.create(self.new_job(), "alice")
        event = self.audit_events("job_create")[0]
        self.assertEqual(event["actor"], "alice")
        self.assertEqual(event["secret_id"], "new_secret")
        self.assertEqual(event["details"]["job"], "new")

    def test_the_new_job_is_rotated_by_a_sweep(self):
        self.admin.create(self.new_job(), "alice")
        results = self.engine.rotate_all_secrets()
        self.assertEqual(list(results), ["db", "api", "new"])

    def test_survives_a_restart(self):
        self.admin.create(self.new_job(), "alice")
        restarted = Settings(config_path=str(self.yaml_path))
        self.assertEqual([j["name"] for j in restarted.get("jobs")], ["db", "api", "new"])

    def test_duplicate_name_is_refused(self):
        with self.assertRaises(JobExistsError):
            self.admin.create(self.new_job(name="db"), "alice")
        self.assertFalse(self.overrides_path.exists())

    def test_every_problem_is_reported_at_once(self):
        with self.assertRaises(JobValidationError) as caught:
            self.admin.create(
                {
                    "name": "x",
                    "provider": "nope",
                    "rotator": "",
                    "secret_id": " bad",
                    "schedule": "hourly",
                    "notification": "yes",
                    "colour": "red",
                },
                "alice",
            )
        fields = caught.exception.fields
        self.assertEqual(
            set(fields), {"provider", "rotator", "secret_id", "schedule", "notification", "colour"}
        )
        self.assertIn("aws, file_storage", fields["provider"])
        self.assertEqual(fields["colour"], "unknown field")
        self.assertFalse(self.overrides_path.exists())
        self.assertEqual(self.audit_events("job_create"), [])

    def test_required_fields(self):
        with self.assertRaises(JobValidationError) as caught:
            self.admin.create({"name": "x"}, "alice")
        self.assertEqual(set(caught.exception.fields), {"provider", "rotator", "secret_id"})

    def test_bad_names(self):
        for bad in (None, "", " x", "a\nb", "x" * 101, 5):
            with self.subTest(bad=bad):
                with self.assertRaises(JobValidationError) as caught:
                    self.admin.create(self.new_job(name=bad), "alice")
                self.assertIn("name", caught.exception.fields)

    def test_non_objects(self):
        for bad in (None, [], "job", 5):
            with self.assertRaises(JobValidationError):
                self.admin.create(bad, "alice")

    def test_secret_id_limits(self):
        for bad in ("x" * 201, "a\x00b", " lead", "trail "):
            with self.assertRaises(JobValidationError) as caught:
                self.admin.create(self.new_job(secret_id=bad), "alice")
            self.assertIn("secret_id", caught.exception.fields)

    def test_two_jobs_cannot_rotate_the_same_secret_in_one_provider(self):
        with self.assertRaises(JobValidationError) as caught:
            self.admin.create(
                self.new_job(provider="file_storage", secret_id="db_password"), "alice"
            )
        self.assertIn("job 'db'", caught.exception.fields["secret_id"])
        # the same secret ID in a different provider is a different secret
        self.admin.create(self.new_job(provider="aws", secret_id="db_password"), "alice")

    def test_stale_revision_is_a_conflict(self):
        self.admin.create(self.new_job(name="a", secret_id="a"), "alice")
        with self.assertRaises(ConflictError):
            self.admin.create(self.new_job(name="b", secret_id="b"), "bob", revision=0)
        self.assertEqual(self.names(), ["db", "api", "a"])
        self.admin.create(
            self.new_job(name="b", secret_id="b"), "bob", revision=self.admin.revision
        )


class TestUpdate(Fixture):
    def test_changes_are_applied_and_reported(self):
        changed = self.admin.update(
            "api",
            {
                "provider": "aws",
                "rotator": "api_key_gen",
                "secret_id": "api_key",
                "schedule": "weekly",
            },
            "alice",
        )
        self.assertEqual(sorted(changed), ["provider", "rotator", "schedule"])
        self.assertEqual(self.job("api")["provider"], "aws")
        self.assertEqual(self.names(), ["db", "api"])  # same place in the list

    def test_editing_a_config_job_leaves_config_yaml_alone(self):
        self.admin.update(
            "db",
            {
                "provider": "file_storage",
                "rotator": "password_gen",
                "secret_id": "db_password",
                "schedule": "daily",
            },
            "alice",
        )
        self.assertEqual(self.yaml_path.read_text(), self.yaml_before)
        self.assertEqual(self.settings.overrides.item_source("jobs", "db"), "modified")
        self.assertEqual(self.job("db")["schedule"], "daily")

    def test_an_update_that_changes_nothing_saves_nothing(self):
        same = {"provider": "file_storage", "rotator": "password_gen", "secret_id": "api_key"}
        self.assertEqual(self.admin.update("api", same, "alice"), [])
        self.assertFalse(self.overrides_path.exists())
        self.assertEqual(self.audit_events("job_update"), [])

    def test_removing_the_schedule_makes_it_follow_the_default(self):
        self.admin.update(
            "db",
            {"provider": "file_storage", "rotator": "password_gen", "secret_id": "db_password"},
            "alice",
        )
        self.assertNotIn("schedule", self.job("db"))
        self.assertNotIn("weekly", self.entry_names())

    def test_the_audit_event_lists_changed_fields_not_values(self):
        self.admin.update(
            "api",
            {
                "provider": "file_storage",
                "rotator": "password_gen",
                "secret_id": "api_key",
                "schedule": "weekly",
            },
            "alice",
        )
        event = self.audit_events("job_update")[0]
        self.assertEqual(event["details"]["changed"], ["schedule"])

    def test_cannot_rename(self):
        with self.assertRaises(JobValidationError) as caught:
            self.admin.update(
                "api",
                {
                    "name": "other",
                    "provider": "file_storage",
                    "rotator": "password_gen",
                    "secret_id": "api_key",
                },
                "alice",
            )
        self.assertIn("renamed", caught.exception.fields["name"])

    def test_unknown_job(self):
        with self.assertRaises(JobNotFoundError):
            self.admin.update(
                "ghost", {"provider": "aws", "rotator": "password_gen", "secret_id": "x"}, "alice"
            )

    def test_invalid_updates_change_nothing(self):
        with self.assertRaises(JobValidationError):
            self.admin.update(
                "api",
                {"provider": "nope", "rotator": "password_gen", "secret_id": "api_key"},
                "alice",
            )
        self.assertFalse(self.overrides_path.exists())
        self.assertEqual(self.job("api")["provider"], "file_storage")

    def test_a_job_can_keep_its_own_provider_and_secret_without_clashing_with_itself(self):
        self.admin.update(
            "api",
            {"provider": "file_storage", "rotator": "api_key_gen", "secret_id": "api_key"},
            "alice",
        )


class TestHiddenJobsAndNormalisation(Fixture):
    def test_hidden_jobs_lists_deleted_config_jobs_only(self):
        self.assertEqual(self.admin.hidden_jobs(), [])
        self.admin.delete("db", "alice")
        self.admin.create(self.new_job(), "alice")
        self.admin.delete("new", "alice")  # a dashboard job is gone, not hidden
        self.assertEqual(self.admin.hidden_jobs(), ["db"])
        self.assertEqual(self.admin.options()["hidden"], ["db"])
        self.admin.reset("db", "alice")
        self.assertEqual(self.admin.hidden_jobs(), [])

    def test_unset_and_off_notifications_are_the_same_thing(self):
        same = {"provider": "file_storage", "rotator": "password_gen", "secret_id": "api_key"}
        self.assertEqual(self.admin.update("api", {**same, "notification": False}, "alice"), [])
        self.assertFalse(self.overrides_path.exists())
        self.assertEqual(
            self.admin.update("api", {**same, "notification": True}, "alice"), ["notification"]
        )
        self.assertEqual(
            self.admin.update("api", {**same, "notification": False}, "alice"), ["notification"]
        )

    def test_detail_exposes_what_the_editor_needs(self):
        status = JobStatusService(
            self.engine,
            audit=self.audit,
            source_of=lambda n: self.settings.overrides.item_source("jobs", n),
        )
        self.admin.set_paused("db", True, "alice")
        config = status.job_detail("db")["config"]
        self.assertEqual(config["own_schedule"], "weekly")
        self.assertIsNone(config["notification"])
        self.assertTrue(config["paused"])
        self.assertEqual(config["source"], "modified")
        self.assertIsNone(status.job_detail("api")["config"]["own_schedule"])


class TestDelete(Fixture):
    def test_deleting_a_dashboard_job(self):
        self.admin.create(self.new_job(schedule="monthly"), "alice")
        self.assertEqual(self.admin.delete("new", "alice"), "dashboard")
        self.assertEqual(self.names(), ["db", "api"])
        self.assertNotIn("new", self.on_disk()["jobs"])
        self.assertNotIn("monthly", self.entry_names())

    def test_deleting_a_config_job_hides_it_without_touching_config_yaml(self):
        self.assertEqual(self.admin.delete("db", "alice"), "config")
        self.assertEqual(self.names(), ["api"])
        self.assertEqual(self.yaml_path.read_text(), self.yaml_before)
        self.assertEqual(self.on_disk()["jobs"]["db"], {"removed": True})
        self.assertNotIn("weekly", self.entry_names())

    def test_audit(self):
        self.admin.delete("db", "alice")
        event = self.audit_events("job_delete")[0]
        self.assertEqual(
            (event["actor"], event["details"]["job"], event["details"]["source"]),
            ("alice", "db", "config"),
        )

    def test_unknown_job(self):
        with self.assertRaises(JobNotFoundError):
            self.admin.delete("ghost", "alice")

    def test_a_deleted_job_is_not_rotated_any_more(self):
        self.admin.delete("db", "alice")
        self.assertEqual(list(self.engine.rotate_all_secrets()), ["api"])


class TestPause(Fixture):
    def test_pausing_marks_the_job(self):
        self.assertTrue(self.admin.set_paused("db", True, "alice"))
        self.assertTrue(self.job("db")["paused"])
        self.assertNotIn("paused", self.job("api"))

    def test_sweeps_skip_paused_jobs(self):
        self.admin.set_paused("db", True, "alice")
        self.assertEqual(list(self.engine.rotate_all_secrets()), ["api"])
        self.assertEqual(list(self.engine.rotate_jobs(["db", "api"])), ["api"])
        self.assertNotIn("db_password", self.provider.store)

    def test_manual_rotation_of_a_paused_job_still_works(self):
        self.admin.set_paused("db", True, "alice")
        self.assertTrue(self.engine.rotate_job("db"))
        self.assertIn("db_password", self.provider.store)

    def test_the_scheduler_does_not_fire_paused_jobs(self):
        self.admin.set_paused("db", True, "alice")
        rotated = []
        self.scheduler.rotate_jobs_function = lambda names: rotated.append(names) or {
            n: True for n in names
        }
        self.scheduler.rotation_function = lambda: rotated.append("ALL") or {}
        schedule.get_jobs(f"{ROTATION_TAG}:weekly")[0].run()
        self.assertEqual(rotated, [])  # the only weekly job is paused
        schedule.get_jobs(f"{ROTATION_TAG}:daily")[0].run()
        self.assertEqual(rotated, [["api"]])

    def test_resume(self):
        self.admin.set_paused("db", True, "alice")
        self.assertTrue(self.admin.set_paused("db", False, "alice"))
        self.assertNotIn("paused", self.job("db"))
        self.assertEqual(list(self.engine.rotate_all_secrets()), ["db", "api"])
        self.assertEqual(self.on_disk()["jobs"], {})

    def test_repeating_the_same_state_changes_nothing(self):
        self.assertFalse(self.admin.set_paused("db", False, "alice"))
        self.admin.set_paused("db", True, "alice")
        revision = self.admin.revision
        self.assertFalse(self.admin.set_paused("db", True, "alice"))
        self.assertEqual(self.admin.revision, revision)

    def test_works_on_dashboard_jobs_and_survives_a_restart(self):
        self.admin.create(self.new_job(), "alice")
        self.admin.set_paused("new", True, "alice")
        restarted = Settings(config_path=str(self.yaml_path))
        paused = {j["name"]: bool(j.get("paused")) for j in restarted.get("jobs")}
        self.assertEqual(paused, {"db": False, "api": False, "new": True})

    def test_audit_events(self):
        self.admin.set_paused("db", True, "alice")
        self.admin.set_paused("db", False, "bob")
        self.assertEqual(
            [e["action"] for e in self.audit_events() if e["action"].startswith("job_")],
            ["job_pause", "job_resume"],
        )

    def test_unknown_job(self):
        with self.assertRaises(JobNotFoundError):
            self.admin.set_paused("ghost", True, "alice")

    def test_status_shows_paused_and_no_next_rotation(self):
        status = JobStatusService(
            self.engine,
            audit=self.audit,
            source_of=lambda n: self.settings.overrides.item_source("jobs", n),
        )
        self.admin.set_paused("db", True, "alice")
        jobs = {j["name"]: j for j in status.job_summaries()}
        self.assertTrue(jobs["db"]["paused"])
        self.assertIsNone(jobs["db"]["next_rotation"])
        self.assertFalse(jobs["api"]["paused"])
        self.assertIsNotNone(jobs["api"]["next_rotation"])


class TestReset(Fixture):
    def test_reset_brings_back_a_modified_config_job(self):
        self.admin.update(
            "api", {"provider": "aws", "rotator": "password_gen", "secret_id": "other"}, "alice"
        )
        self.assertEqual(self.admin.reset("api", "alice"), "modified")
        self.assertEqual(self.job("api")["secret_id"], "api_key")
        self.assertEqual(self.job("api")["provider"], "file_storage")

    def test_reset_brings_back_a_hidden_config_job(self):
        self.admin.delete("db", "alice")
        self.assertEqual(self.admin.reset("db", "alice"), "removed")
        self.assertEqual(self.names(), ["db", "api"])
        self.assertIn("weekly", self.entry_names())

    def test_reset_deletes_a_dashboard_job(self):
        self.admin.create(self.new_job(), "alice")
        self.admin.reset("new", "alice")
        self.assertEqual(self.names(), ["db", "api"])

    def test_reset_also_clears_a_pause(self):
        self.admin.set_paused("db", True, "alice")
        self.admin.reset("db", "alice")
        self.assertNotIn("paused", self.job("db"))

    def test_nothing_to_reset(self):
        with self.assertRaises(JobValidationError):
            self.admin.reset("db", "alice")
        with self.assertRaises(JobNotFoundError):
            self.admin.reset("ghost", "alice")


class TestEngineJobList(unittest.TestCase):
    def test_invalid_entries_are_skipped(self):
        engine = RotationEngine()
        count = engine.replace_rotation_jobs(
            [
                {"name": "ok", "provider": "p", "rotator": "r", "secret_id": "s"},
                {"name": "bad"},
                "junk",
                {"provider": "p"},
            ]
        )
        self.assertEqual(count, 1)
        self.assertEqual([j["name"] for j in engine.rotation_jobs], ["ok"])

    def test_swapping_does_not_disturb_a_running_sweep_copy(self):
        engine = RotationEngine()
        engine.replace_rotation_jobs(
            [{"name": "a", "provider": "p", "rotator": "r", "secret_id": "s"}]
        )
        snapshot = list(engine.rotation_jobs)
        engine.replace_rotation_jobs([])
        self.assertEqual(len(snapshot), 1)


class TestActivityMessages(unittest.TestCase):
    def test_job_actions_read_naturally(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLog(audit_file=str(Path(tmp) / "audit.log"))
            for action in (
                "job_create",
                "job_update",
                "job_delete",
                "job_pause",
                "job_resume",
                "job_reset",
            ):
                audit.log(action, "alice", details={"job": "db"})
            messages = [e["message"] for e in ActivityService(audit=audit).query()["events"]]
        self.assertEqual(
            messages[::-1],
            [
                "alice created job db",
                "alice changed job db",
                "alice deleted job db",
                "alice paused job db",
                "alice resumed job db",
                "alice reset job db",
            ],
        )


USERNAME = "jobs-api-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)


class TestJobAdminApi(Fixture):
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

    def make_app(self, csrf=False):
        app = create_app(self.engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = csrf
        app.job_admin = self.admin
        app.job_status = JobStatusService(
            self.engine,
            audit=self.audit,
            source_of=lambda n: self.settings.overrides.item_source("jobs", n),
        )
        limiter.reset()
        return app

    def setUp(self):
        super().setUp()
        self.client = self.make_app().test_client()

    def login(self):
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def test_every_endpoint_requires_login(self):
        for method, url in (
            ("post", "/api/jobs"),
            ("put", "/api/jobs/db"),
            ("delete", "/api/jobs/db"),
            ("post", "/api/jobs/db/pause"),
            ("post", "/api/jobs/db/resume"),
            ("post", "/api/jobs/db/reset"),
            ("get", "/api/job-options"),
        ):
            self.assertEqual(getattr(self.client, method)(url, json={}).status_code, 401, url)
        self.assertEqual(self.names(), ["db", "api"])

    def test_options(self):
        self.login()
        data = self.client.get("/api/job-options").get_json()
        self.assertEqual(data["providers"], ["aws", "file_storage"])

    def test_create(self):
        self.login()
        resp = self.client.post("/api/jobs", json=self.new_job(schedule="weekly"))
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data["job"]["name"], "new")
        self.assertEqual(data["job"]["source"], "dashboard")
        self.assertEqual(data["job"]["schedule"], "weekly")
        self.assertEqual(data["revision"], 1)
        self.assertEqual(self.audit_events("job_create")[0]["actor"], USERNAME)

    def test_create_validation_errors_are_400_with_fields(self):
        self.login()
        resp = self.client.post("/api/jobs", json={"name": "x", "provider": "nope"})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data["error"], "Invalid job")
        self.assertIn("provider", data["fields"])
        self.assertIn("rotator", data["fields"])

    def test_create_requires_a_json_object(self):
        self.login()
        for kwargs in (
            {"data": "name=x"},
            {"json": [1, 2]},
            {"data": "{broken", "content_type": "application/json"},
        ):
            self.assertEqual(self.client.post("/api/jobs", **kwargs).status_code, 400)

    def test_duplicate_name_is_409(self):
        self.login()
        resp = self.client.post("/api/jobs", json=self.new_job(name="db"))
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["fields"], {"name": "already exists"})

    def test_revision_conflict_is_409(self):
        self.login()
        self.client.post("/api/jobs", json=self.new_job(name="a", secret_id="a"))
        resp = self.client.post(
            "/api/jobs", json={**self.new_job(name="b", secret_id="b"), "revision": 0}
        )
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(resp.get_json()["conflict"])
        ok = self.client.post(
            "/api/jobs", json={**self.new_job(name="b", secret_id="b"), "revision": 1}
        )
        self.assertEqual(ok.status_code, 201)

    def test_a_non_integer_revision_is_ignored(self):
        self.login()
        resp = self.client.post("/api/jobs", json={**self.new_job(), "revision": "0"})
        self.assertEqual(resp.status_code, 201)

    def test_update(self):
        self.login()
        resp = self.client.put(
            "/api/jobs/api",
            json={"provider": "aws", "rotator": "password_gen", "secret_id": "api_key"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["changed"], ["provider"])
        self.assertEqual(data["job"]["source"], "modified")

    def test_update_unknown_job_is_404_and_invalid_is_400(self):
        self.login()
        body = {"provider": "aws", "rotator": "password_gen", "secret_id": "x"}
        self.assertEqual(self.client.put("/api/jobs/ghost", json=body).status_code, 404)
        self.assertEqual(
            self.client.put("/api/jobs/api", json={"provider": "nope"}).status_code, 400
        )

    def test_delete(self):
        self.login()
        resp = self.client.delete("/api/jobs/db")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["source"], "config")
        self.assertEqual(self.names(), ["api"])
        self.assertEqual(self.client.delete("/api/jobs/db").status_code, 404)

    def test_pause_and_resume(self):
        self.login()
        paused = self.client.post("/api/jobs/db/pause").get_json()
        self.assertTrue(paused["changed"])
        self.assertTrue(paused["job"]["paused"])
        again = self.client.post("/api/jobs/db/pause").get_json()
        self.assertFalse(again["changed"])
        resumed = self.client.post("/api/jobs/db/resume").get_json()
        self.assertFalse(resumed["job"]["paused"])
        self.assertEqual(self.client.post("/api/jobs/ghost/pause").status_code, 404)

    def test_reset(self):
        self.login()
        self.client.delete("/api/jobs/db")
        resp = self.client.post("/api/jobs/db/reset")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["job"]["name"], "db")
        self.assertEqual(self.client.post("/api/jobs/db/reset").status_code, 400)
        self.assertEqual(self.client.post("/api/jobs/ghost/reset").status_code, 404)

    def test_names_with_slashes(self):
        self.login()
        self.assertEqual(
            self.client.post(
                "/api/jobs", json=self.new_job(name="prod/db", secret_id="p")
            ).status_code,
            201,
        )
        self.assertEqual(self.client.post("/api/jobs/prod%2Fdb/pause").status_code, 200)
        self.assertEqual(
            self.client.put(
                "/api/jobs/prod%2Fdb",
                json={"provider": "aws", "rotator": "api_key_gen", "secret_id": "p2"},
            ).status_code,
            200,
        )
        self.assertEqual(self.client.delete("/api/jobs/prod%2Fdb").status_code, 200)

    def test_existing_job_endpoints_still_work(self):
        self.login()
        self.assertEqual(self.client.get("/api/jobs").status_code, 200)
        self.assertEqual(self.client.post("/api/jobs/nope/rotate").status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/db/detail").status_code, 200)

    def test_writes_are_rate_limited(self):
        self.login()
        codes = [self.client.post("/api/jobs/db/pause").status_code for _ in range(31)]
        self.assertEqual(codes[-1], 429)

    def test_csrf_is_enforced(self):
        client = self.make_app(csrf=True).test_client()
        page = client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        client.post(
            "/login", data={"username": USERNAME, "password": PASSWORD, "csrf_token": token}
        )
        self.assertEqual(client.post("/api/jobs", json=self.new_job()).status_code, 400)
        self.assertEqual(self.names(), ["db", "api"])
        home = client.get("/").get_data(as_text=True)
        header = re.search(r'name="csrf-token" content="([^"]+)"', home).group(1)
        resp = client.post("/api/jobs", json=self.new_job(), headers={"X-CSRFToken": header})
        self.assertEqual(resp.status_code, 201)

    def test_responses_contain_no_secret_values(self):
        self.login()
        body = self.client.post("/api/jobs", json=self.new_job()).get_data(as_text=True)
        body += self.client.get("/api/job-options").get_data(as_text=True)
        self.assertNotIn("new-value", body)


if __name__ == "__main__":
    unittest.main()

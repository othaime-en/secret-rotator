"""Tests for the dashboard settings file (config/overrides.py) and its
integration with Settings."""

import copy
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from secret_rotator.config.overrides import (
    SOURCE_CONFIG,
    SOURCE_DASHBOARD,
    SOURCE_MODIFIED,
    SOURCE_REMOVED,
    ConflictError,
    OverridesError,
    OverridesStore,
    validate_name,
    validate_setting_key,
)
from secret_rotator.config.settings import DEFAULT_OVERRIDES_FILE, Settings

BASE = {
    "rotation": {"schedule": "daily", "retry_attempts": 3},
    "providers": {"file_storage": {"type": "file", "file_path": "data/secrets.json"}},
    "rotators": {"password_gen": {"type": "password", "length": 16}},
    "jobs": [
        {"name": "db", "provider": "file_storage", "rotator": "password_gen", "secret_id": "db"},
        {"name": "api", "provider": "file_storage", "rotator": "password_gen", "secret_id": "api"},
    ],
}


class StoreFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "settings" / "dashboard-settings.json"
        self.base = copy.deepcopy(BASE)
        self.store = self.make()

    def make(self):
        return OverridesStore(self.path, base_provider=lambda: self.base)

    def effective(self, store=None):
        store = store or self.store
        store.load()
        return store.apply(self.base)

    def on_disk(self):
        return json.loads(self.path.read_text())

    def names(self, config):
        return [j["name"] for j in config["jobs"]]


class TestValidation(unittest.TestCase):
    def test_names(self):
        self.assertEqual(validate_name("prod/db password-1.x"), "prod/db password-1.x")
        for bad in ("", " x", "x ", "a\nb", "a\x00b", "x" * 101, 5, None):
            with self.subTest(bad=bad):
                with self.assertRaises(OverridesError):
                    validate_name(bad)

    def test_setting_keys(self):
        self.assertEqual(validate_setting_key("rotation.retry_attempts"), "rotation.retry_attempts")
        for bad in ("", ".x", "x.", "a..b", "a b", "a.b-c", "a/b", "../x", 5, "a.b.c.d.e.f.g"):
            with self.subTest(bad=bad):
                with self.assertRaises(OverridesError):
                    validate_setting_key(bad)


class TestLoading(StoreFixture):
    def test_missing_file_means_no_overrides(self):
        self.assertEqual(self.effective(), BASE)
        self.assertIsNone(self.store.load_error)
        self.assertEqual(self.store.revision, 0)

    def test_invalid_json_is_ignored_with_an_error(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json")
        self.assertEqual(self.effective(), BASE)
        self.assertIn("not valid JSON", self.store.load_error)

    def test_wrong_shapes_and_versions_are_ignored(self):
        self.path.parent.mkdir(parents=True)
        for content in ("[]", '{"version": 99}', "42", "null", '{"jobs": {}}'):
            with self.subTest(content=content):
                self.path.write_text(content)
                self.assertEqual(self.effective(), BASE)
                self.assertIsNotNone(self.store.load_error)

    def test_bad_entries_are_dropped_with_warnings_good_ones_kept(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "revision": 3,
                    "jobs": {
                        "good": {"definition": {"provider": "p", "rotator": "r"}},
                        "bad1": "not an object",
                        "bad2": {"definition": "nope"},
                        "bad3": {"paused": "yes"},
                        " bad4": {"removed": True},
                    },
                    "settings": {"rotation.retry_attempts": 9, "bad key": 1},
                }
            )
        )
        config = self.effective()
        self.assertIn("good", self.names(config))
        self.assertNotIn("bad1", self.names(config))
        self.assertEqual(config["rotation"]["retry_attempts"], 9)
        self.assertEqual(len(self.store.warnings), 5)
        self.assertIsNone(self.store.load_error)
        self.assertEqual(self.store.revision, 3)

    def test_unreadable_file_is_reported_not_raised(self):
        self.path.parent.mkdir(parents=True)
        self.path.mkdir()  # a directory where the file should be
        self.assertEqual(self.effective(), BASE)
        self.assertIn("cannot read", self.store.load_error)


class TestMerging(StoreFixture):
    def test_base_is_never_modified(self):
        self.store.set_item("jobs", "db", {"provider": "x", "rotator": "y"}, "alice")
        self.store.set_setting("rotation.retry_attempts", 9, "alice")
        self.effective()
        self.assertEqual(self.base, BASE)

    def test_adds_a_job_after_the_config_jobs(self):
        self.store.set_item("jobs", "new", {"provider": "p", "rotator": "r"}, "alice")
        self.assertEqual(self.names(self.effective()), ["db", "api", "new"])

    def test_replaces_a_config_job_in_place(self):
        self.store.set_item(
            "jobs", "db", {"provider": "other", "rotator": "r", "schedule": "weekly"}, "alice"
        )
        jobs = self.effective()["jobs"]
        self.assertEqual(self.names({"jobs": jobs}), ["db", "api"])
        self.assertEqual(jobs[0]["provider"], "other")
        self.assertEqual(jobs[0]["schedule"], "weekly")
        self.assertNotIn("secret_id", jobs[0])  # a replacement, not a patch

    def test_job_name_in_the_definition_is_forced_to_the_key(self):
        self.store.set_item("jobs", "new", {"name": "spoofed", "provider": "p"}, "alice")
        self.assertEqual(self.names(self.effective())[-1], "new")
        self.assertEqual(self.on_disk()["jobs"]["new"]["definition"]["name"], "new")

    def test_removing_a_config_job_hides_it(self):
        self.store.remove_item("jobs", "db", "alice")
        self.assertEqual(self.names(self.effective()), ["api"])
        self.assertEqual(self.on_disk()["jobs"]["db"], {"removed": True})
        self.assertEqual(self.base["jobs"][0]["name"], "db")

    def test_removing_a_dashboard_job_deletes_it_outright(self):
        self.store.set_item("jobs", "new", {"provider": "p"}, "alice")
        self.store.remove_item("jobs", "new", "alice")
        self.assertNotIn("new", self.on_disk()["jobs"])
        self.assertEqual(self.names(self.effective()), ["db", "api"])

    def test_pausing_marks_the_job_and_resuming_clears_it(self):
        self.store.set_paused("db", True, "alice")
        jobs = {j["name"]: j for j in self.effective()["jobs"]}
        self.assertTrue(jobs["db"]["paused"])
        self.assertNotIn("paused", jobs["api"])
        self.assertTrue(self.store.is_paused("db"))
        self.store.set_paused("db", False, "alice")
        self.assertNotIn("db", self.on_disk()["jobs"])  # nothing left to store
        self.assertFalse(self.store.is_paused("db"))

    def test_pausing_keeps_a_replacement_definition(self):
        self.store.set_item("jobs", "db", {"provider": "other"}, "alice")
        self.store.set_paused("db", True, "alice")
        self.store.set_paused("db", False, "alice")
        self.assertEqual(self.effective()["jobs"][0]["provider"], "other")

    def test_a_definition_cannot_smuggle_in_paused(self):
        self.store.set_item("jobs", "new", {"provider": "p", "paused": True}, "alice")
        self.assertNotIn("paused", self.on_disk()["jobs"]["new"]["definition"])

    def test_providers_and_rotators(self):
        self.store.set_item("providers", "aws", {"type": "aws", "region": "eu-west-1"}, "alice")
        self.store.set_item("rotators", "password_gen", {"type": "password", "length": 32}, "alice")
        self.store.remove_item("providers", "file_storage", "alice")
        config = self.effective()
        self.assertEqual(set(config["providers"]), {"aws"})
        self.assertEqual(config["rotators"]["password_gen"]["length"], 32)

    def test_setting_overrides_create_nested_keys(self):
        self.store.set_setting("rotation.retry_attempts", 9, "alice")
        self.store.set_setting("notifications.slack.channel", "#ops", "alice")
        config = self.effective()
        self.assertEqual(config["rotation"], {"schedule": "daily", "retry_attempts": 9})
        self.assertEqual(config["notifications"]["slack"]["channel"], "#ops")

    def test_setting_override_replaces_a_scalar_parent(self):
        self.base["logging"] = "oops"
        self.store.set_setting("logging.level", "DEBUG", "alice")
        self.assertEqual(self.effective()["logging"], {"level": "DEBUG"})

    def test_reset_setting_restores_the_config_value(self):
        self.store.set_setting("rotation.retry_attempts", 9, "alice")
        self.store.reset_setting("rotation.retry_attempts", "alice")
        self.assertEqual(self.effective()["rotation"]["retry_attempts"], 3)
        self.assertEqual(self.store.overridden_settings(), {})

    def test_reset_item_restores_a_removed_or_replaced_config_job(self):
        self.store.remove_item("jobs", "db", "alice")
        self.store.reset_item("jobs", "db", "alice")
        self.assertEqual(self.effective()["jobs"][0], BASE["jobs"][0])
        self.store.set_item("jobs", "api", {"provider": "other"}, "alice")
        self.store.reset_item("jobs", "api", "alice")
        self.assertEqual(self.effective()["jobs"][1], BASE["jobs"][1])

    def test_untouched_sections_are_not_invented(self):
        self.base = {"rotation": {"schedule": "daily"}}
        self.store.set_setting("rotation.retry_attempts", 5, "alice")
        config = self.effective()
        self.assertNotIn("jobs", config)
        self.assertNotIn("providers", config)

    def test_non_dict_jobs_in_config_are_passed_through(self):
        self.base["jobs"].append("garbage")
        self.assertIn("garbage", self.effective()["jobs"])

    def test_item_source(self):
        self.store.set_item("jobs", "new", {"provider": "p"}, "alice")
        self.store.set_item("jobs", "api", {"provider": "other"}, "alice")
        self.store.remove_item("jobs", "db", "alice")
        self.store.load()
        self.assertEqual(self.store.item_source("jobs", "new"), SOURCE_DASHBOARD)
        self.assertEqual(self.store.item_source("jobs", "api"), SOURCE_MODIFIED)
        self.assertEqual(self.store.item_source("jobs", "db"), SOURCE_REMOVED)
        self.assertIsNone(self.store.item_source("jobs", "ghost"))
        self.assertEqual(self.store.item_source("providers", "file_storage"), SOURCE_CONFIG)
        self.store.set_paused("api", True, "alice")  # pausing a config job counts as modified
        self.store.reset_item("jobs", "api", "alice")
        self.store.set_paused("api", True, "alice")
        self.assertEqual(self.store.item_source("jobs", "api"), SOURCE_MODIFIED)


class TestChangesAreRejectedWhenInvalid(StoreFixture):
    def test_bad_inputs(self):
        for call in (
            lambda: self.store.set_item("widgets", "x", {}, "a"),
            lambda: self.store.set_item("jobs", "", {}, "a"),
            lambda: self.store.set_item("jobs", "x", "not a dict", "a"),
            lambda: self.store.set_item("jobs", "x", {"v": object()}, "a"),
            lambda: self.store.set_item("jobs", "x", {"v": "y" * 30_000}, "a"),
            lambda: self.store.set_setting("bad key", 1, "a"),
            lambda: self.store.set_setting("a.b", object(), "a"),
            lambda: self.store.set_setting("a.b", "y" * 20_000, "a"),
            lambda: self.store.remove_item("jobs", "a\nb", "a"),
            lambda: self.store.set_paused(" x", True, "a"),
        ):
            with self.assertRaises(OverridesError):
                call()
        self.assertFalse(self.path.exists())  # nothing was written


class TestPersistence(StoreFixture):
    def test_changes_survive_a_new_store(self):
        self.store.set_item("jobs", "new", {"provider": "p"}, "alice")
        other = self.make()
        self.assertEqual(self.names(self.effective(other)), ["db", "api", "new"])

    def test_revision_and_audit_fields(self):
        self.assertEqual(self.store.set_item("jobs", "a", {}, "alice"), 1)
        self.assertEqual(self.store.set_setting("x.y", 1, "bob"), 2)
        data = self.on_disk()
        self.assertEqual((data["version"], data["revision"], data["updated_by"]), (1, 2, "bob"))
        self.assertIsNotNone(data["updated_at"])
        self.assertEqual((self.store.revision, self.store.updated_by), (2, "bob"))

    def test_file_is_private(self):
        self.store.set_setting("x.y", 1, "alice")
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_previous_version_is_kept_as_a_backup(self):
        self.store.set_setting("x.y", 1, "alice")
        self.store.set_setting("x.y", 2, "alice")
        backup = json.loads(self.path.with_name(self.path.name + ".bak").read_text())
        self.assertEqual(backup["settings"], {"x.y": 1})
        self.assertEqual(self.on_disk()["settings"], {"x.y": 2})
        self.assertEqual(
            stat.S_IMODE(os.stat(self.path.with_name(self.path.name + ".bak")).st_mode), 0o600
        )

    def test_a_failed_write_leaves_the_file_intact_and_no_temp_files(self):
        self.store.set_setting("x.y", 1, "alice")
        before = self.path.read_text()
        real_replace = os.replace

        def failing_replace(src, dst):
            if str(src).endswith(".tmp"):
                raise OSError("disk full")
            return real_replace(src, dst)

        with patch("secret_rotator.config.overrides.os.replace", side_effect=failing_replace):
            with self.assertRaises(OSError):
                self.store.set_setting("x.y", 2, "alice")
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual([p.name for p in self.path.parent.glob("*.tmp")], [])

    def test_values_are_never_logged(self):
        with self.assertLogs("secret-rotator.config.overrides", level="INFO") as logs:
            self.store.set_item(
                "rotators", "db", {"type": "database", "password": "hunter2"}, "alice"
            )
            self.store.set_setting("notifications.smtp_password", "s3cret-value", "alice")
        text = "\n".join(logs.output)
        self.assertIn("rotators/db saved", text)
        self.assertNotIn("hunter2", text)
        self.assertNotIn("s3cret-value", text)

    def test_a_corrupt_file_is_moved_aside_not_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{broken")
        self.store.set_setting("x.y", 1, "alice")
        self.assertEqual(self.on_disk()["settings"], {"x.y": 1})
        aside = list(self.path.parent.glob("dashboard-settings.json.corrupt-*"))
        self.assertEqual(len(aside), 1)
        self.assertEqual(aside[0].read_text(), "{broken")
        self.assertIsNone(self.store.load_error)


class TestConcurrency(StoreFixture):
    def test_stale_expected_revision_is_a_conflict(self):
        self.store.set_setting("a.b", 1, "alice")
        loaded = self.store.revision
        self.make().set_setting("a.c", 2, "bob")  # someone else, meanwhile
        with self.assertRaises(ConflictError):
            self.store.set_setting("a.d", 3, "alice", expected_revision=loaded)
        self.assertEqual(self.on_disk()["settings"], {"a.b": 1, "a.c": 2})

    def test_matching_expected_revision_succeeds(self):
        self.store.set_setting("a.b", 1, "alice")
        self.store.set_setting("a.c", 2, "alice", expected_revision=1)
        self.assertEqual(self.store.revision, 2)

    def test_independent_stores_do_not_overwrite_each_other(self):
        first, second = self.make(), self.make()
        first.set_setting("a.one", 1, "alice")
        second.set_setting("a.two", 2, "bob")  # never loaded; must re-read
        self.assertEqual(self.on_disk()["settings"], {"a.one": 1, "a.two": 2})

    def test_many_writers_lose_nothing(self):
        errors = []

        def work(i):
            try:
                self.make().set_item("jobs", f"job{i:02d}", {"provider": "p"}, f"user{i}")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        data = self.on_disk()
        self.assertEqual(len(data["jobs"]), 16)
        self.assertEqual(data["revision"], 16)


class TestSettingsIntegration(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.yaml_path = self.dir / "config.yaml"
        self.yaml_path.write_text(yaml.safe_dump(BASE))
        self.overrides_path = self.dir / "dashboard-settings.json"
        env = patch.dict(
            os.environ, {"SECRET_ROTATOR_DASHBOARD_SETTINGS": str(self.overrides_path)}
        )
        env.start()
        self.addCleanup(env.stop)

    def settings(self):
        return Settings(config_path=str(self.yaml_path))

    def test_get_sees_the_dashboards_changes(self):
        settings = self.settings()
        self.assertEqual(settings.get("rotation.retry_attempts"), 3)
        settings.overrides.set_setting("rotation.retry_attempts", 9, "alice")
        settings.reload()
        self.assertEqual(settings.get("rotation.retry_attempts"), 9)
        self.assertEqual([j["name"] for j in settings.get("jobs")], ["db", "api"])

    def test_a_fresh_process_starts_with_the_overrides_applied(self):
        first = self.settings()
        first.overrides.set_item("jobs", "new", {"provider": "p", "rotator": "r"}, "alice")
        second = self.settings()
        self.assertEqual([j["name"] for j in second.get("jobs")], ["db", "api", "new"])

    def test_base_config_stays_as_read_from_config_yaml(self):
        settings = self.settings()
        settings.overrides.set_setting("rotation.retry_attempts", 9, "alice")
        settings.reload()
        self.assertEqual(settings.base_config["rotation"]["retry_attempts"], 3)

    def test_config_yaml_is_never_written_by_the_dashboard(self):
        before = self.yaml_path.read_text()
        settings = self.settings()
        settings.overrides.set_item("jobs", "new", {"provider": "p"}, "alice")
        settings.overrides.remove_item("jobs", "db", "alice")
        settings.overrides.set_setting("rotation.schedule", "weekly", "alice")
        self.assertEqual(self.yaml_path.read_text(), before)

    def test_save_writes_only_config_yamls_own_contents(self):
        settings = self.settings()
        settings.overrides.set_setting("rotation.retry_attempts", 9, "alice")
        settings.overrides.set_item("jobs", "dash", {"provider": "p"}, "alice")
        settings.reload()
        settings.set("web.auth.username", "admin")  # what the CLI does
        self.assertTrue(settings.save())
        saved = yaml.safe_load(self.yaml_path.read_text())
        self.assertEqual(saved["web"]["auth"]["username"], "admin")
        self.assertEqual(saved["rotation"]["retry_attempts"], 3)
        self.assertEqual([j["name"] for j in saved["jobs"]], ["db", "api"])
        self.assertEqual(settings.get("web.auth.username"), "admin")

    def test_set_is_visible_through_get_before_any_save(self):
        settings = self.settings()
        settings.set("distributed.enabled", True)
        self.assertTrue(settings.get("distributed.enabled"))

    def test_reload_overrides_picks_up_dashboard_changes_and_keeps_in_memory_sets(self):
        worker = self.settings()
        worker.set("distributed.enabled", True)
        other_process = self.settings()
        other_process.overrides.set_item("jobs", "new", {"provider": "p"}, "alice")
        self.assertEqual(len(worker.get("jobs")), 2)
        worker.reload_overrides()
        self.assertEqual(len(worker.get("jobs")), 3)
        self.assertTrue(worker.get("distributed.enabled"))

    def test_reload_overrides_does_not_reread_config_yaml(self):
        worker = self.settings()
        self.yaml_path.write_text(yaml.safe_dump({**BASE, "rotation": {"schedule": "weekly"}}))
        worker.reload_overrides()
        self.assertEqual(worker.get("rotation.schedule"), "daily")
        worker.reload()
        self.assertEqual(worker.get("rotation.schedule"), "weekly")

    def test_a_broken_overrides_file_does_not_break_startup(self):
        self.overrides_path.write_text("{broken")
        settings = self.settings()
        self.assertEqual(settings.get("rotation.schedule"), "daily")
        self.assertIn("not valid JSON", settings.overrides.load_error)

    def test_path_can_come_from_config_yaml(self):
        elsewhere = self.dir / "elsewhere.json"
        self.yaml_path.write_text(
            yaml.safe_dump({**BASE, "dashboard": {"settings_file": str(elsewhere)}})
        )
        with patch.dict(os.environ):
            os.environ.pop("SECRET_ROTATOR_DASHBOARD_SETTINGS")
            settings = self.settings()
        self.assertEqual(settings.overrides.path, elsewhere)

    def test_environment_variable_beats_config_yaml(self):
        self.yaml_path.write_text(
            yaml.safe_dump({**BASE, "dashboard": {"settings_file": "ignored.json"}})
        )
        self.assertEqual(self.settings().overrides.path, self.overrides_path)

    def test_default_path_is_in_the_data_directory(self):
        with patch.dict(os.environ):
            os.environ.pop("SECRET_ROTATOR_DASHBOARD_SETTINGS")
            settings = self.settings()
        self.assertEqual(settings.overrides.path, Path(DEFAULT_OVERRIDES_FILE))
        self.assertEqual(DEFAULT_OVERRIDES_FILE, "data/dashboard-settings.json")


class TestWorkerReload(unittest.TestCase):
    def test_run_rotation_job_picks_up_dashboard_changes_first(self):
        from secret_rotator import job_queue

        order = []

        def build():
            order.append("build")
            raise RuntimeError("stop here")

        with (
            patch.object(
                job_queue.settings, "reload_overrides", side_effect=lambda: order.append("reload")
            ),
            patch("secret_rotator.bootstrap.build_rotation_engine", side_effect=build),
        ):
            with self.assertRaises(RuntimeError):
                job_queue.run_rotation_job("tester")
        self.assertEqual(order, ["reload", "build"])


if __name__ == "__main__":
    unittest.main()

"""Tests for the provider/rotator catalog and validation, ComponentAdminService
and the /api/providers and /api/rotators endpoints."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from werkzeug.security import generate_password_hash

from secret_rotator import bootstrap
from secret_rotator.activity import ActivityService
from secret_rotator.audit_log import AuditLog
from secret_rotator.component_admin import (
    ComponentAdminService,
    ComponentExistsError,
    ComponentInUseError,
    ComponentNotFoundError,
    ComponentValidationError,
)
from secret_rotator.component_catalog import (
    KIND_PROVIDERS,
    KIND_ROTATORS,
    PROVIDER_TYPES,
    ROTATOR_TYPES,
    defaults,
    validate_fields,
    validate_file_path,
)
from secret_rotator.config.overrides import ConflictError
from secret_rotator.config.settings import Settings
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.job_admin import JobAdminService
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter


class TestCatalog(unittest.TestCase):
    def check(self, spec, definition, **kwargs):
        problems = {}
        canonical = validate_fields(spec, definition, problems, **kwargs)
        return canonical, problems

    def test_defaults(self):
        self.assertEqual(defaults(KIND_ROTATORS, "password")["length"], 16)
        self.assertEqual(defaults(KIND_ROTATORS, "api_key")["format"], "hex")
        self.assertEqual(defaults(KIND_PROVIDERS, "file")["file_path"], "data/secrets.json")

    def test_integers_are_bounded_and_not_booleans_or_text(self):
        spec = ROTATOR_TYPES["password"]
        for good in (8, 16, 256):
            self.assertEqual(self.check(spec, {"length": good})[1], {})
        for bad in (7, 257, "16", 16.5, True, [16]):
            self.assertIn("length", self.check(spec, {"length": bad})[1], bad)

    def test_booleans_must_be_booleans(self):
        spec = ROTATOR_TYPES["password"]
        for bad in ("true", 1, 0, "yes"):
            self.assertIn("use_symbols", self.check(spec, {"use_symbols": bad})[1], bad)

    def test_choices(self):
        spec = ROTATOR_TYPES["api_key"]
        self.assertEqual(self.check(spec, {"format": "base64"})[0], {"format": "base64"})
        self.assertIn("format", self.check(spec, {"format": "rot13"})[1])
        self.assertIn(
            "algorithm", self.check(ROTATOR_TYPES["jwt_secret"], {"algorithm": "none"})[1]
        )

    def test_prefix_text(self):
        spec = ROTATOR_TYPES["api_key"]
        self.assertEqual(self.check(spec, {"prefix": "sk_live-1.x"})[1], {})
        for bad in ("sk live", "sk/live", "x" * 33, "é", 5, "a;b"):
            self.assertIn("prefix", self.check(spec, {"prefix": bad})[1], bad)

    def test_unknown_fields_are_rejected_so_nothing_else_can_be_stored(self):
        for sneaky in (
            "password",
            "api_token",
            "aws_secret_access_key",
            "host",
            "enabled",
            "__class__",
        ):
            _, problems = self.check(ROTATOR_TYPES["password"], {sneaky: "x"})
            self.assertEqual(problems, {sneaky: "unknown field"})

    def test_name_and_type_are_not_unknown(self):
        self.assertEqual(
            self.check(ROTATOR_TYPES["password"], {"name": "x", "type": "password"})[1], {}
        )

    def test_none_means_not_given(self):
        canonical, problems = self.check(ROTATOR_TYPES["password"], {"length": None})
        self.assertEqual((canonical, problems), ({}, {}))

    def test_the_file_provider_path_is_required(self):
        self.assertEqual(
            self.check(PROVIDER_TYPES["file"], {}, root=Path("."), protected=[])[1],
            {"file_path": "required"},
        )

    def test_describe_is_json_serialisable_and_complete(self):
        for spec in list(PROVIDER_TYPES.values()) + list(ROTATOR_TYPES.values()):
            described = json.loads(json.dumps(spec.describe()))
            self.assertEqual(described["type"], spec.key)
            for field in described["fields"]:
                self.assertIn(field["kind"], ("int", "bool", "choice", "text", "path"))
                self.assertIn("label", field)

    def test_only_buildable_types_are_offered(self):
        self.assertEqual(set(PROVIDER_TYPES), {"file"})
        self.assertEqual(set(ROTATOR_TYPES), {"password", "api_key", "jwt_secret"})


class TestFilePath(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "data"
        self.root.mkdir()
        self.outside = Path(tmp.name) / "outside"
        self.outside.mkdir()
        self.protected = [
            self.root / ".master.key",
            self.root / "backup",
            self.root / "dashboard-settings.json",
        ]

    def check(self, value):
        return validate_file_path(value, self.root, self.protected)

    def test_a_json_file_inside_the_root_is_fine(self):
        for name in ("secrets.json", "sub/dir/more.json"):
            self.assertEqual(self.check(str(self.root / name)), (str(self.root / name), None), name)

    def test_outside_the_root_is_refused(self):
        for value in (
            str(self.outside / "x.json"),
            "/etc/x.json",
            str(self.root / ".." / "outside" / "x.json"),
            "../x.json",
        ):
            self.assertIn("inside the data directory", self.check(value)[1], value)

    def test_symlinks_cannot_escape(self):
        (self.root / "link").symlink_to(self.outside, target_is_directory=True)
        self.assertIn(
            "inside the data directory", self.check(str(self.root / "link" / "x.json"))[1]
        )

    def test_must_be_json(self):
        for name in ("secrets", "secrets.txt", "secrets.json.bak", ".master.key"):
            self.assertEqual(self.check(str(self.root / name))[1], "must be a .json file", name)

    def test_directories_are_refused(self):
        (self.root / "dir.json").mkdir()
        self.assertEqual(self.check(str(self.root / "dir.json"))[1], "is a directory")

    def test_the_daemons_own_files_are_refused(self):
        self.assertIn(
            "used by the daemon", self.check(str(self.root / "dashboard-settings.json"))[1]
        )
        self.assertIn("used by the daemon", self.check(str(self.root / "backup" / "x.json"))[1])

    def test_bad_values(self):
        for value in (None, "", 5, ["x.json"], "a\x00b.json", "x" * 300 + ".json"):
            self.assertIsNotNone(self.check(value)[1], value)


class Fixture(unittest.TestCase):
    def setUp(self):
        DistributedLock.reset_local_locks()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.data = self.dir / "data"
        self.data.mkdir()
        self.config = {
            "dashboard": {"provider_root": str(self.data)},
            "security": {
                "encryption": {"enabled": False, "master_key_file": str(self.data / ".master.key")}
            },
            "backup": {"storage_path": str(self.data / "backup")},
            "providers": {
                "file_storage": {"type": "file", "file_path": str(self.data / "secrets.json")},
                "aws_secrets": {"type": "aws", "region": "us-east-1"},
            },
            "rotators": {
                "password_gen": {"type": "password", "length": 16},
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
        self.yaml_path = self.dir / "config.yaml"
        self.yaml_path.write_text(yaml.safe_dump(self.config))
        self.yaml_before = self.yaml_path.read_text()

        env = patch.dict(
            os.environ,
            {"SECRET_ROTATOR_DASHBOARD_SETTINGS": str(self.data / "dashboard-settings.json")},
        )
        env.start()
        self.addCleanup(env.stop)
        self.settings = Settings(config_path=str(self.yaml_path))
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

    def audit_events(self, prefix):
        return [e for e in self.audit.read_events() if e["action"].startswith(prefix)]

    def disk(self):
        return json.loads((self.data / "dashboard-settings.json").read_text())

    def provider(self, **overrides):
        data = {"name": "extra", "type": "file", "file_path": str(self.data / "extra.json")}
        data.update(overrides)
        return data

    def rotator(self, **overrides):
        data = {"name": "strong", "type": "password", "length": 24}
        data.update(overrides)
        return data


class TestListing(Fixture):
    def test_providers(self):
        listing = self.admin.listing(KIND_PROVIDERS)
        items = {i["name"]: i for i in listing["items"]}
        file_storage = items["file_storage"]
        self.assertEqual(
            (
                file_storage["type"],
                file_storage["supported"],
                file_storage["registered"],
                file_storage["source"],
            ),
            ("file", True, True, "config"),
        )
        self.assertEqual(file_storage["used_by"], ["db"])
        self.assertEqual(
            file_storage["summary"],
            [{"label": "File path", "value": str(self.data / "secrets.json")}],
        )
        aws = items["aws_secrets"]
        self.assertEqual((aws["supported"], aws["registered"]), (False, False))
        self.assertEqual(aws["summary"], [])
        self.assertEqual(listing["types"][0]["type"], "file")
        self.assertEqual(listing["hidden"], [])
        self.assertEqual(listing["revision"], 0)

    def test_rotators_show_defaults_and_only_safe_settings(self):
        items = {i["name"]: i for i in self.admin.listing(KIND_ROTATORS)["items"]}
        password = items["password_gen"]
        self.assertEqual(password["fields"]["length"], 16)
        self.assertEqual(password["fields"]["use_symbols"], True)  # default filled in
        self.assertEqual(password["used_by"], ["db"])
        self.assertIn({"label": "Length", "value": "16"}, password["summary"])
        legacy = items["legacy_db"]
        self.assertEqual(
            (legacy["supported"], legacy["registered"], legacy["fields"]), (False, False, {})
        )
        self.assertNotIn("db.internal", json.dumps(self.admin.listing(KIND_ROTATORS)))

    def test_describe_does_not_leak_unlisted_config_keys(self):
        self.config["providers"]["file_storage"]["password"] = "hunter2"
        self.yaml_path.write_text(yaml.safe_dump(self.config))
        self.settings.reload()
        self.assertNotIn("hunter2", json.dumps(self.admin.listing(KIND_PROVIDERS)))


class TestCreateProvider(Fixture):
    def test_creates_registers_and_saves_outside_config_yaml(self):
        name = self.admin.create(KIND_PROVIDERS, self.provider(), "alice")
        self.assertEqual(name, "extra")
        self.assertIn("extra", self.engine.providers)
        self.assertTrue(self.engine.providers["extra"].validate_connection())
        self.assertEqual(
            self.disk()["providers"]["extra"]["definition"],
            {"type": "file", "file_path": str(self.data / "extra.json")},
        )
        self.assertEqual(self.yaml_path.read_text(), self.yaml_before)
        self.assertEqual(self.settings.overrides.item_source("providers", "extra"), "dashboard")

    def test_survives_a_restart(self):
        self.admin.create(KIND_PROVIDERS, self.provider(), "alice")
        self.assertIn("extra", Settings(config_path=str(self.yaml_path)).get("providers"))

    def test_audit(self):
        self.admin.create(KIND_PROVIDERS, self.provider(), "alice")
        event = self.audit_events("provider_")[0]
        self.assertEqual(
            (event["action"], event["actor"], event["details"]),
            ("provider_create", "alice", {"name": "extra", "type": "file"}),
        )

    def test_the_new_provider_can_be_used_by_a_job(self):
        self.admin.create(KIND_PROVIDERS, self.provider(), "alice")
        jobs = JobAdminService(self.engine, self.settings, audit=self.audit)
        self.assertIn("extra", jobs.options()["providers"])
        jobs.create(
            {"name": "j2", "provider": "extra", "rotator": "password_gen", "secret_id": "s"},
            "alice",
        )

    def test_validation_reports_everything(self):
        with self.assertRaises(ComponentValidationError) as caught:
            self.admin.create(
                KIND_PROVIDERS,
                {"name": "x", "type": "file", "file_path": "/etc/cron.d/x.txt", "api_key": "AKIA"},
                "alice",
            )
        self.assertEqual(set(caught.exception.fields), {"file_path", "api_key"})
        self.assertFalse((self.data / "dashboard-settings.json").exists())

    def test_types_that_do_nothing_are_refused(self):
        for bad in ("aws", "azure", "", None, 5):
            with self.assertRaises(ComponentValidationError) as caught:
                self.admin.create(
                    KIND_PROVIDERS, {"name": "x", "type": bad, "region": "eu"}, "alice"
                )
            self.assertIn("type", caught.exception.fields)

    def test_paths_outside_the_data_directory_are_refused(self):
        for path in ("/etc/passwd.json", str(self.dir / "elsewhere.json"), "../x.json"):
            with self.assertRaises(ComponentValidationError) as caught:
                self.admin.create(KIND_PROVIDERS, self.provider(file_path=path), "alice")
            self.assertIn("file_path", caught.exception.fields, path)

    def test_the_daemons_own_files_cannot_be_provider_files(self):
        for path in (self.data / "dashboard-settings.json", self.data / "backup" / "x.json"):
            with self.assertRaises(ComponentValidationError):
                self.admin.create(KIND_PROVIDERS, self.provider(file_path=str(path)), "alice")

    def test_duplicates_and_bad_names(self):
        with self.assertRaises(ComponentExistsError):
            self.admin.create(KIND_PROVIDERS, self.provider(name="file_storage"), "alice")
        for bad in (None, "", " x", "a\nb", "x" * 101):
            with self.assertRaises(ComponentValidationError) as caught:
                self.admin.create(KIND_PROVIDERS, self.provider(name=bad), "alice")
            self.assertIn("name", caught.exception.fields)

    def test_stale_revision(self):
        self.admin.create(KIND_PROVIDERS, self.provider(), "alice")
        with self.assertRaises(ConflictError):
            self.admin.create(
                KIND_PROVIDERS,
                self.provider(name="other", file_path=str(self.data / "o.json")),
                "bob",
                revision=0,
            )
        self.assertNotIn("other", self.engine.providers)


class TestCreateRotator(Fixture):
    def test_each_buildable_type_works_end_to_end(self):
        for name, definition in (
            ("p", {"type": "password", "length": 24}),
            ("k", {"type": "api_key", "length": 40, "format": "base64", "prefix": "sk_"}),
            ("j", {"type": "jwt_secret", "algorithm": "HS512"}),
        ):
            self.admin.create(KIND_ROTATORS, {"name": name, **definition}, "alice")
            value = self.engine.rotators[name].generate_new_secret()
            self.assertTrue(self.engine.rotators[name].validate_secret(value), name)
        self.assertEqual(len(self.engine.rotators["p"].generate_new_secret()), 24)
        self.assertTrue(self.engine.rotators["k"].generate_new_secret().startswith("sk_"))

    def test_saved_outside_config_yaml(self):
        self.admin.create(KIND_ROTATORS, self.rotator(), "alice")
        self.assertEqual(self.yaml_path.read_text(), self.yaml_before)
        self.assertEqual(
            self.disk()["rotators"]["strong"]["definition"], {"type": "password", "length": 24}
        )

    def test_settings_that_cannot_work_are_refused_up_front(self):
        with self.assertRaises(ComponentValidationError) as caught:
            self.admin.create(
                KIND_ROTATORS,
                self.rotator(
                    use_lowercase=False, use_uppercase=False, use_numbers=False, use_symbols=False
                ),
                "alice",
            )
        self.assertIn("do not work together", caught.exception.fields["_"])
        self.assertNotIn("strong", self.engine.rotators)
        self.assertFalse((self.data / "dashboard-settings.json").exists())

    def test_field_errors(self):
        for definition, field in (
            ({"length": 7}, "length"),
            ({"length": "24"}, "length"),
            ({"use_symbols": "yes"}, "use_symbols"),
            ({"colour": "red"}, "colour"),
            ({"type": "api_key", "prefix": "bad prefix!"}, "prefix"),
            ({"type": "jwt_secret", "algorithm": "none"}, "algorithm"),
        ):
            with self.assertRaises(ComponentValidationError) as caught:
                self.admin.create(
                    KIND_ROTATORS, {"name": "r", "type": "password", **definition}, "alice"
                )
            self.assertIn(field, caught.exception.fields, definition)

    def test_types_that_are_not_built_at_startup_are_refused(self):
        for bad in ("database", "ssh_key", "certificate", "oauth2"):
            with self.assertRaises(ComponentValidationError) as caught:
                self.admin.create(KIND_ROTATORS, {"name": "x", "type": bad}, "alice")
            self.assertIn("password, api_key, jwt_secret", caught.exception.fields["type"])

    def test_no_credentials_can_be_smuggled_in(self):
        for key in ("password", "secret", "token", "private_key", "connection_string"):
            with self.assertRaises(ComponentValidationError):
                self.admin.create(KIND_ROTATORS, self.rotator(**{key: "hunter2"}), "alice")


class TestUpdate(Fixture):
    def test_changes_are_applied_live(self):
        changed = self.admin.update(
            KIND_ROTATORS, "password_gen", {"type": "password", "length": 32}, "alice"
        )
        self.assertEqual(changed, ["length"])
        self.assertEqual(len(self.engine.rotators["password_gen"].generate_new_secret()), 32)
        self.assertEqual(
            self.settings.overrides.item_source("rotators", "password_gen"), "modified"
        )

    def test_changing_a_provider_file_replaces_the_registered_provider(self):
        before = self.engine.providers["file_storage"]
        self.admin.update(
            KIND_PROVIDERS,
            "file_storage",
            {"type": "file", "file_path": str(self.data / "moved.json")},
            "alice",
        )
        self.assertIsNot(self.engine.providers["file_storage"], before)
        self.assertEqual(
            str(self.engine.providers["file_storage"].file_path), str(self.data / "moved.json")
        )

    def test_an_update_that_changes_nothing_saves_nothing(self):
        self.assertEqual(
            self.admin.update(
                KIND_ROTATORS, "password_gen", {"type": "password", "length": 16}, "alice"
            ),
            [],
        )
        self.assertEqual(
            self.admin.update(
                KIND_ROTATORS, "password_gen", {"type": "password", "use_symbols": True}, "alice"
            ),
            [],
        )
        self.assertFalse((self.data / "dashboard-settings.json").exists())
        self.assertEqual(self.audit_events("rotator_"), [])

    def test_audit_lists_changed_field_names_not_values(self):
        self.admin.update(
            KIND_ROTATORS, "password_gen", {"type": "password", "length": 40}, "alice"
        )
        event = self.audit_events("rotator_")[0]
        self.assertEqual(
            (event["action"], event["details"]["changed"]), ("rotator_update", ["length"])
        )
        self.assertNotIn("40", json.dumps(event["details"]))

    def test_type_and_name_cannot_change(self):
        with self.assertRaises(ComponentValidationError) as caught:
            self.admin.update(KIND_ROTATORS, "password_gen", {"type": "api_key"}, "alice")
        self.assertIn("cannot be changed", caught.exception.fields["type"])
        with self.assertRaises(ComponentValidationError) as caught:
            self.admin.update(
                KIND_ROTATORS, "password_gen", {"name": "renamed", "type": "password"}, "alice"
            )
        self.assertIn("name", caught.exception.fields)

    def test_unknown_and_unsupported(self):
        with self.assertRaises(ComponentNotFoundError):
            self.admin.update(KIND_ROTATORS, "ghost", {"type": "password"}, "alice")
        with self.assertRaises(ComponentValidationError):
            self.admin.update(KIND_ROTATORS, "legacy_db", {"type": "database"}, "alice")

    def test_a_bad_update_leaves_the_running_rotator_alone(self):
        before = self.engine.rotators["password_gen"]
        with self.assertRaises(ComponentValidationError):
            self.admin.update(
                KIND_ROTATORS, "password_gen", {"type": "password", "length": 3}, "alice"
            )
        self.assertIs(self.engine.rotators["password_gen"], before)


class TestDeleteAndReset(Fixture):
    def test_components_in_use_cannot_be_deleted(self):
        with self.assertRaises(ComponentInUseError) as caught:
            self.admin.delete(KIND_PROVIDERS, "file_storage", "alice")
        self.assertEqual(caught.exception.jobs, ["db"])
        with self.assertRaises(ComponentInUseError):
            self.admin.delete(KIND_ROTATORS, "password_gen", "alice")
        self.assertIn("file_storage", self.engine.providers)
        self.assertFalse((self.data / "dashboard-settings.json").exists())

    def test_deleting_a_dashboard_item(self):
        self.admin.create(KIND_ROTATORS, self.rotator(), "alice")
        self.assertEqual(self.admin.delete(KIND_ROTATORS, "strong", "alice"), "dashboard")
        self.assertNotIn("strong", self.engine.rotators)
        self.assertNotIn("strong", self.disk()["rotators"])

    def test_deleting_an_unused_config_item_hides_it_and_reset_restores_it(self):
        self.assertEqual(self.admin.delete(KIND_PROVIDERS, "aws_secrets", "alice"), "config")
        self.assertEqual(self.yaml_path.read_text(), self.yaml_before)
        self.assertEqual(self.admin.listing(KIND_PROVIDERS)["hidden"], ["aws_secrets"])
        self.assertNotIn(
            "aws_secrets", [i["name"] for i in self.admin.listing(KIND_PROVIDERS)["items"]]
        )
        self.assertEqual(self.admin.reset(KIND_PROVIDERS, "aws_secrets", "alice"), "removed")
        self.assertEqual(self.admin.listing(KIND_PROVIDERS)["hidden"], [])

    def test_reset_returns_a_modified_item_to_config_yaml(self):
        self.admin.update(
            KIND_ROTATORS, "password_gen", {"type": "password", "length": 64}, "alice"
        )
        self.admin.reset(KIND_ROTATORS, "password_gen", "alice")
        self.assertEqual(len(self.engine.rotators["password_gen"].generate_new_secret()), 16)

    def test_reset_of_a_dashboard_item_in_use_is_refused(self):
        self.admin.create(KIND_ROTATORS, self.rotator(), "alice")
        JobAdminService(self.engine, self.settings, audit=self.audit).create(
            {"name": "j2", "provider": "file_storage", "rotator": "strong", "secret_id": "s"},
            "alice",
        )
        with self.assertRaises(ComponentInUseError):
            self.admin.reset(KIND_ROTATORS, "strong", "alice")
        with self.assertRaises(ComponentInUseError):
            self.admin.delete(KIND_ROTATORS, "strong", "alice")

    def test_nothing_to_reset_and_unknown(self):
        with self.assertRaises(ComponentValidationError):
            self.admin.reset(KIND_ROTATORS, "password_gen", "alice")
        with self.assertRaises(ComponentNotFoundError):
            self.admin.reset(KIND_ROTATORS, "ghost", "alice")
        with self.assertRaises(ComponentNotFoundError):
            self.admin.delete(KIND_ROTATORS, "ghost", "alice")

    def test_an_unsupported_config_item_can_be_hidden(self):
        self.admin.delete(KIND_ROTATORS, "legacy_db", "alice")
        self.assertIn("legacy_db", self.admin.listing(KIND_ROTATORS)["hidden"])


class TestTryingThingsOut(Fixture):
    def test_provider_test(self):
        self.assertEqual(
            self.admin.test_provider("file_storage"), {"ok": True, "message": "Connected."}
        )

    def test_provider_test_when_the_provider_is_broken(self):
        with patch.object(
            self.engine.providers["file_storage"], "validate_connection", return_value=False
        ):
            result = self.admin.test_provider("file_storage")
        self.assertFalse(result["ok"])

    def test_provider_test_never_leaks_the_error(self):
        with patch.object(
            self.engine.providers["file_storage"],
            "validate_connection",
            side_effect=RuntimeError("/srv/secret/path boom"),
        ):
            result = self.admin.test_provider("file_storage")
        self.assertFalse(result["ok"])
        self.assertNotIn("/srv", json.dumps(result))

    def test_unavailable_and_unknown_providers(self):
        self.assertFalse(self.admin.test_provider("aws_secrets")["ok"])
        with self.assertRaises(ComponentNotFoundError):
            self.admin.test_provider("ghost")

    def test_sample_of_a_saved_rotator_has_its_shape(self):
        self.admin.update(
            KIND_ROTATORS, "password_gen", {"type": "password", "length": 30}, "alice"
        )
        sample = self.admin.sample_rotator(name="password_gen")
        self.assertEqual(len(sample["sample"]), 30)
        self.assertEqual(sample["type"], "password")

    def test_sample_of_unsaved_settings(self):
        sample = self.admin.sample_rotator(
            definition={"type": "api_key", "prefix": "sk_live_", "length": 20, "name": "ignored"}
        )
        self.assertTrue(sample["sample"].startswith("sk_live_"))
        self.assertEqual(
            self.admin.sample_rotator(definition={"type": "jwt_secret"})["type"], "jwt_secret"
        )
        self.assertFalse((self.data / "dashboard-settings.json").exists())  # nothing was saved

    def test_unsaved_settings_are_validated_first(self):
        with self.assertRaises(ComponentValidationError):
            self.admin.sample_rotator(definition={"type": "password", "length": 2})
        with self.assertRaises(ComponentValidationError):
            self.admin.sample_rotator(definition="not a dict")

    def test_samples_are_random_and_never_audited_or_stored(self):
        first = self.admin.sample_rotator(name="password_gen")["sample"]
        second = self.admin.sample_rotator(name="password_gen")["sample"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.audit.read_events(), [])

    def test_unknown_or_unavailable_rotators(self):
        with self.assertRaises(ComponentNotFoundError):
            self.admin.sample_rotator(name="ghost")
        self.assertIsNone(self.admin.sample_rotator(name="legacy_db")["sample"])


class TestActivityMessages(unittest.TestCase):
    def test_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLog(audit_file=str(Path(tmp) / "audit.log"))
            for action in ("provider_create", "provider_update", "rotator_delete", "rotator_reset"):
                audit.log(action, "alice", details={"name": "x", "type": "file"})
            messages = [e["message"] for e in ActivityService(audit=audit).query()["events"]][::-1]
        self.assertEqual(
            messages,
            [
                "alice created provider x",
                "alice changed provider x",
                "alice deleted rotator x",
                "alice reset rotator x",
            ],
        )


USERNAME = "components-admin"
PASSWORD = "correct-horse-battery-staple"
ENV_KEYS = (
    "SECRET_ROTATOR_ADMIN_USERNAME",
    "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
    "FLASK_SECRET_KEY",
    "SECRET_ROTATOR_ENV",
)


class TestComponentApi(Fixture):
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
        app.component_admin = self.admin
        # create_app wires job management to the process-wide settings; use
        # this test's own so nothing is written outside the temp directory.
        app.job_admin = JobAdminService(self.engine, self.settings, audit=self.audit)
        self.client = app.test_client()
        limiter.reset()

    def login(self):
        self.client.post("/login", data={"username": USERNAME, "password": PASSWORD})

    def test_every_endpoint_requires_login(self):
        for method, url in (
            ("get", "/api/providers"),
            ("post", "/api/providers"),
            ("put", "/api/providers/x"),
            ("delete", "/api/providers/x"),
            ("post", "/api/providers/x/reset"),
            ("post", "/api/providers/x/test"),
            ("get", "/api/rotators"),
            ("post", "/api/rotators"),
            ("put", "/api/rotators/x"),
            ("delete", "/api/rotators/x"),
            ("post", "/api/rotators/x/reset"),
            ("post", "/api/rotators/x/sample"),
            ("post", "/api/rotators/sample"),
        ):
            self.assertEqual(getattr(self.client, method)(url, json={}).status_code, 401, url)

    def test_listing(self):
        self.login()
        data = self.client.get("/api/providers").get_json()
        self.assertEqual({i["name"] for i in data["items"]}, {"file_storage", "aws_secrets"})
        self.assertEqual(data["types"][0]["fields"][0]["name"], "file_path")
        rotators = self.client.get("/api/rotators").get_json()
        self.assertEqual(
            {t["type"] for t in rotators["types"]}, {"password", "api_key", "jwt_secret"}
        )

    def test_create_provider(self):
        self.login()
        resp = self.client.post("/api/providers", json=self.provider())
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(
            (data["item"]["name"], data["item"]["source"], data["item"]["registered"]),
            ("extra", "dashboard", True),
        )
        self.assertEqual(data["revision"], 1)
        self.assertEqual(self.audit_events("provider_")[0]["actor"], USERNAME)

    def test_create_rotator_and_use_it_in_a_job(self):
        self.login()
        self.assertEqual(self.client.post("/api/rotators", json=self.rotator()).status_code, 201)
        job = {"name": "j2", "provider": "file_storage", "rotator": "strong", "secret_id": "s"}
        self.assertEqual(self.client.post("/api/jobs", json=job).status_code, 201)

    def test_validation_errors_are_400_with_fields(self):
        self.login()
        resp = self.client.post(
            "/api/rotators", json={"name": "x", "type": "password", "length": 2, "colour": "red"}
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data["error"], "Invalid rotator")
        self.assertEqual(set(data["fields"]), {"length", "colour"})
        probe = self.client.post(
            "/api/rotators",
            json=self.rotator(
                use_lowercase=False, use_uppercase=False, use_numbers=False, use_symbols=False
            ),
        )
        self.assertIn("_", probe.get_json()["fields"])

    def test_body_must_be_json(self):
        self.login()
        self.assertEqual(self.client.post("/api/providers", data="x=1").status_code, 400)
        self.assertEqual(self.client.put("/api/providers/file_storage", json=[1]).status_code, 400)

    def test_duplicates_and_revision_conflicts_are_409(self):
        self.login()
        self.assertEqual(
            self.client.post("/api/providers", json=self.provider(name="file_storage")).status_code,
            409,
        )
        self.client.post("/api/rotators", json=self.rotator())
        resp = self.client.post("/api/rotators", json={**self.rotator(name="r2"), "revision": 0})
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(resp.get_json()["conflict"])
        self.assertNotIn("r2", self.engine.rotators)
        self.assertEqual(
            self.client.post(
                "/api/rotators", json={**self.rotator(name="r2"), "revision": 1}
            ).status_code,
            201,
        )

    def test_update(self):
        self.login()
        resp = self.client.put(
            "/api/rotators/password_gen", json={"type": "password", "length": 40}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["changed"], ["length"])
        self.assertEqual(
            self.client.put("/api/rotators/ghost", json={"type": "password"}).status_code, 404
        )
        self.assertEqual(
            self.client.put("/api/rotators/password_gen", json={"type": "api_key"}).status_code, 400
        )

    def test_delete_in_use_is_409_with_the_jobs(self):
        self.login()
        resp = self.client.delete("/api/providers/file_storage")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["jobs"], ["db"])
        self.assertEqual(self.client.delete("/api/providers/aws_secrets").status_code, 200)
        self.assertEqual(self.client.delete("/api/providers/ghost").status_code, 404)

    def test_reset(self):
        self.login()
        self.client.delete("/api/providers/aws_secrets")
        resp = self.client.post("/api/providers/aws_secrets/reset")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["item"]["name"], "aws_secrets")
        self.assertEqual(self.client.post("/api/providers/aws_secrets/reset").status_code, 400)
        self.client.post("/api/rotators", json=self.rotator())
        deleted = self.client.post("/api/rotators/strong/reset").get_json()
        self.assertIsNone(deleted["item"])

    def test_provider_test_and_samples(self):
        self.login()
        self.assertTrue(self.client.post("/api/providers/file_storage/test").get_json()["ok"])
        self.assertEqual(self.client.post("/api/providers/ghost/test").status_code, 404)
        saved = self.client.post("/api/rotators/password_gen/sample").get_json()
        self.assertEqual(len(saved["sample"]), 16)
        unsaved = self.client.post(
            "/api/rotators/sample", json={"type": "api_key", "prefix": "k_", "length": 20}
        ).get_json()
        self.assertTrue(unsaved["sample"].startswith("k_"))
        self.assertEqual(
            self.client.post(
                "/api/rotators/sample", json={"type": "password", "length": 1}
            ).status_code,
            400,
        )
        self.assertEqual(self.client.post("/api/rotators/ghost/sample").status_code, 404)

    def test_a_rotator_named_sample_or_test_is_still_manageable(self):
        self.login()
        self.assertEqual(
            self.client.post("/api/rotators", json=self.rotator(name="sample")).status_code, 201
        )
        self.assertEqual(
            self.client.put(
                "/api/rotators/sample", json={"type": "password", "length": 30}
            ).status_code,
            200,
        )
        self.assertEqual(self.client.delete("/api/rotators/sample").status_code, 200)

    def test_names_with_slashes(self):
        self.login()
        self.assertEqual(
            self.client.post("/api/rotators", json=self.rotator(name="team/a")).status_code, 201
        )
        self.assertEqual(
            self.client.put(
                "/api/rotators/team%2Fa", json={"type": "password", "length": 30}
            ).status_code,
            200,
        )
        self.assertEqual(self.client.post("/api/rotators/team%2Fa/sample").status_code, 200)
        self.assertEqual(self.client.delete("/api/rotators/team%2Fa").status_code, 200)

    def test_provider_test_is_rate_limited(self):
        self.login()
        codes = [
            self.client.post("/api/providers/file_storage/test").status_code for _ in range(11)
        ]
        self.assertEqual(codes[-1], 429)

    def test_responses_never_contain_config_keys_outside_the_catalog(self):
        self.login()
        body = self.client.get("/api/rotators").get_data(as_text=True) + self.client.get(
            "/api/providers"
        ).get_data(as_text=True)
        self.assertNotIn("db.internal", body)

    def test_existing_endpoints_are_unchanged(self):
        self.login()
        self.assertEqual(self.client.get("/api/jobs").status_code, 200)


if __name__ == "__main__":
    unittest.main()

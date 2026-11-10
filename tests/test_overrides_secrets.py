"""Tests for encrypted secret settings in the dashboard settings file."""

import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from cryptography.fernet import Fernet

from secret_rotator.config.overrides import (
    SECRET_SETTING_KEYS,
    WRAPPER_KEY,
    OverridesError,
    OverridesStore,
)
from secret_rotator.config.settings import Settings

SECRET_ID = "backup.remote_backup.access_key_id"
SECRET_KEY = "backup.remote_backup.secret_access_key"
REDIS = "distributed.redis_url"
PLAINTEXT = "AKIA-TOP-SECRET-VALUE"


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "data" / "dashboard-settings.json"
        self.base = {"backup": {"remote_backup": {"enabled": True, "bucket": "b"}}}
        self.store = self.make()

    def make(self, **kwargs):
        return OverridesStore(self.path, base_provider=lambda: self.base, **kwargs)

    def effective(self, store=None):
        store = store or self.store
        store.load()
        return store.apply(self.base)

    def raw(self):
        return self.path.read_text()

    def every_file_text(self):
        return "\n".join(
            p.read_text(errors="replace")
            for p in self.path.parent.iterdir()
            if p.is_file() and p.suffix != ".key"
        )


class TestEncryptedStorage(Fixture):
    def test_the_secret_keys_are_the_credentials(self):
        self.assertEqual(SECRET_SETTING_KEYS, {SECRET_ID, SECRET_KEY, REDIS})

    def test_stored_encrypted_and_never_as_plain_text(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.assertNotIn(PLAINTEXT, self.every_file_text())
        stored = json.loads(self.raw())["settings"][SECRET_KEY]
        self.assertEqual(set(stored), {WRAPPER_KEY})
        self.assertNotEqual(stored[WRAPPER_KEY], PLAINTEXT)

    def test_the_rest_of_the_program_sees_the_plain_value(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        config = self.effective()
        self.assertEqual(config["backup"]["remote_backup"]["secret_access_key"], PLAINTEXT)
        self.assertEqual(config["backup"]["remote_backup"]["bucket"], "b")

    def test_survives_a_new_store(self):
        self.store.set_setting(REDIS, "redis://:hunter2@cache:6379/0", "alice")
        config = self.effective(self.make())
        self.assertEqual(config["distributed"]["redis_url"], "redis://:hunter2@cache:6379/0")

    def test_each_value_is_encrypted_separately_and_unpredictably(self):
        self.store.set_setting(SECRET_ID, PLAINTEXT, "alice")
        first = json.loads(self.raw())["settings"][SECRET_ID][WRAPPER_KEY]
        self.store.set_setting(SECRET_ID, PLAINTEXT, "alice")
        second = json.loads(self.raw())["settings"][SECRET_ID][WRAPPER_KEY]
        self.assertNotEqual(first, second)  # Fernet uses a fresh IV every time

    def test_the_backup_copy_holds_only_ciphertext_too(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.store.set_setting("rotation.retry_attempts", 5, "alice")
        self.assertTrue(self.path.with_name(self.path.name + ".bak").exists())
        self.assertNotIn(PLAINTEXT, self.every_file_text())

    def test_values_never_reach_the_logs(self):
        with self.assertLogs("secret-rotator.config.overrides", level="INFO") as logs:
            self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.assertNotIn(PLAINTEXT, "\n".join(logs.output))
        self.assertIn(
            "setting backup.remote_backup.secret_access_key changed", "\n".join(logs.output)
        )

    def test_ordinary_settings_are_still_plain_json(self):
        self.store.set_setting("rotation.retry_attempts", 5, "alice")
        self.assertEqual(json.loads(self.raw())["settings"], {"rotation.retry_attempts": 5})
        self.assertFalse(self.store.key_path.exists())  # no key until a secret is saved

    def test_reset_removes_the_secret(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.store.reset_setting(SECRET_KEY, "alice")
        self.assertNotIn(SECRET_KEY, json.loads(self.raw())["settings"])
        self.assertNotIn("secret_access_key", self.effective()["backup"]["remote_backup"])

    def test_the_dashboard_value_beats_config_yaml(self):
        self.base["backup"]["remote_backup"]["secret_access_key"] = "from-config-yaml"
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.assertEqual(
            self.effective()["backup"]["remote_backup"]["secret_access_key"], PLAINTEXT
        )
        self.store.reset_setting(SECRET_KEY, "alice")
        self.assertEqual(
            self.effective()["backup"]["remote_backup"]["secret_access_key"], "from-config-yaml"
        )


class TestWhatIsListedAndReturned(Fixture):
    def test_overridden_settings_never_includes_secrets(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.store.set_setting("rotation.retry_attempts", 5, "alice")
        self.assertEqual(self.store.overridden_settings(), {"rotation.retry_attempts": 5})
        self.assertNotIn(PLAINTEXT, json.dumps(self.store.overridden_settings()))

    def test_secrets_set_lists_keys_only(self):
        self.assertEqual(self.store.secrets_set(), [])
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.store.set_setting(SECRET_ID, "id", "alice")
        self.assertEqual(self.store.secrets_set(), [SECRET_ID, SECRET_KEY])

    def test_item_listing_helpers_do_not_leak(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.assertNotIn(
            PLAINTEXT, repr(self.store.overridden_settings()) + repr(self.store.secrets_set())
        )


class TestKeyFile(Fixture):
    def test_created_on_first_secret_with_private_permissions(self):
        self.assertFalse(self.store.key_path.exists())
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.assertEqual(self.store.key_path, self.path.parent / "dashboard-settings.key")
        self.assertEqual(stat.S_IMODE(os.stat(self.store.key_path).st_mode), 0o600)
        Fernet(self.store.key_path.read_bytes())  # a valid Fernet key

    def test_it_is_not_the_master_key(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.assertNotEqual(self.store.key_path.name, ".master.key")

    def test_custom_key_location(self):
        custom = self.dir / "keys" / "settings.key"
        store = self.make(key_path=custom)
        store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.assertTrue(custom.exists())
        self.assertFalse((self.path.parent / "dashboard-settings.key").exists())

    def test_the_same_key_is_reused(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        key = self.store.key_path.read_bytes()
        self.make().set_setting(SECRET_ID, "id", "bob")
        self.assertEqual(self.store.key_path.read_bytes(), key)

    def test_concurrent_first_use_creates_one_key(self):
        stores = [self.make() for _ in range(12)]
        errors = []

        def work(i):
            try:
                stores[i].set_setting(SECRET_KEY, f"value-{i}", f"user{i}")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        # whichever write came last is readable with the one key on disk
        value = self.effective(self.make())["backup"]["remote_backup"]["secret_access_key"]
        self.assertTrue(value.startswith("value-"))

    def test_key_creation_leaves_no_temporary_files_and_a_private_key(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        leftovers = [p.name for p in self.store.key_path.parent.glob("*.tmp")]
        self.assertEqual(leftovers, [])
        if os.name == "posix":
            self.assertEqual(os.stat(self.store.key_path).st_mode & 0o777, 0o600)

    def test_loose_permissions_are_reported(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        os.chmod(self.store.key_path, 0o644)
        fresh = self.make()
        with self.assertLogs("secret-rotator.config.overrides", level="WARNING") as logs:
            self.effective(fresh)
        self.assertIn("chmod 600", "\n".join(logs.output))

    def test_an_invalid_key_file_refuses_new_secrets_without_overwriting_it(self):
        self.store.key_path.parent.mkdir(parents=True)
        self.store.key_path.write_text("not a fernet key")
        with self.assertRaises(OverridesError) as caught:
            self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.assertIn("not a valid key", str(caught.exception))
        self.assertEqual(self.store.key_path.read_text(), "not a fernet key")
        self.assertFalse(self.path.exists())


class TestFailureModes(Fixture):
    def test_a_lost_key_means_the_secret_is_ignored_with_a_warning(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.store.set_setting("rotation.retry_attempts", 5, "alice")
        self.store.key_path.unlink()
        fresh = self.make()
        config = self.effective(fresh)
        self.assertNotIn("secret_access_key", config["backup"]["remote_backup"])
        self.assertEqual(config["rotation"]["retry_attempts"], 5)  # nothing else is affected
        self.assertTrue(any("missing or unreadable" in w for w in fresh.warnings))
        self.assertEqual(fresh.secrets_set(), [])

    def test_a_different_key_means_the_secret_is_ignored(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.store.key_path.write_bytes(Fernet.generate_key())
        fresh = self.make()
        self.assertNotIn("secret_access_key", self.effective(fresh)["backup"]["remote_backup"])
        self.assertTrue(any("cannot be decrypted" in w for w in fresh.warnings))

    def test_a_tampered_value_is_ignored(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        data = json.loads(self.raw())
        token = data["settings"][SECRET_KEY][WRAPPER_KEY]
        data["settings"][SECRET_KEY][WRAPPER_KEY] = token[:-4] + (
            "AAAA" if token[-4:] != "AAAA" else "BBBB"
        )
        self.path.write_text(json.dumps(data))
        fresh = self.make()
        self.assertNotIn("secret_access_key", self.effective(fresh)["backup"]["remote_backup"])
        self.assertTrue(fresh.warnings)

    def test_plain_text_found_for_a_secret_is_ignored_not_used(self):
        self.store.set_setting("rotation.retry_attempts", 5, "alice")
        data = json.loads(self.raw())
        data["settings"][SECRET_KEY] = "hand-edited-plain-value"
        self.path.write_text(json.dumps(data))
        fresh = self.make()
        config = self.effective(fresh)
        self.assertNotIn("secret_access_key", config["backup"]["remote_backup"])
        self.assertTrue(any("stored as plain text" in w for w in fresh.warnings))

    def test_a_wrapper_under_an_ordinary_key_is_ignored(self):
        self.store.set_setting("rotation.retry_attempts", 5, "alice")
        data = json.loads(self.raw())
        data["settings"]["rotation.timeout"] = {WRAPPER_KEY: "gAAAA"}
        self.path.write_text(json.dumps(data))
        fresh = self.make()
        self.assertNotIn("timeout", self.effective(fresh).get("rotation", {}))
        self.assertTrue(any("not a secret setting" in w for w in fresh.warnings))

    def test_warnings_are_not_repeated(self):
        self.store.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.store.key_path.unlink()
        fresh = self.make()
        fresh.load()
        fresh.apply(self.base)
        fresh.apply(self.base)
        self.assertEqual(sum("missing or unreadable" in w for w in fresh.warnings), 1)


class TestValidation(Fixture):
    def test_secret_values_must_be_non_empty_text(self):
        for bad in ("", None, 5, ["x"], {"a": 1}, True):
            with self.assertRaises(OverridesError):
                self.store.set_setting(SECRET_KEY, bad, "alice")
        self.assertFalse(self.path.exists())
        self.assertFalse(self.store.key_path.exists())

    def test_length_and_control_characters(self):
        for bad in ("x" * 5000, "a\nb", "a\x00b"):
            with self.assertRaises(OverridesError):
                self.store.set_setting(SECRET_KEY, bad, "alice")

    def test_the_wrapper_cannot_be_smuggled_into_an_ordinary_setting(self):
        with self.assertRaises(OverridesError):
            self.store.set_setting("rotation.retry_attempts", {WRAPPER_KEY: "gAAAA"}, "alice")

    def test_a_wrapper_cannot_be_given_as_a_secrets_value(self):
        with self.assertRaises(OverridesError):
            self.store.set_setting(SECRET_KEY, {WRAPPER_KEY: "gAAAA"}, "alice")

    def test_other_keys_can_be_declared_secret(self):
        store = self.make(secret_keys=frozenset({"notifications.smtp_password"}))
        store.set_setting("notifications.smtp_password", PLAINTEXT, "alice")
        self.assertNotIn(PLAINTEXT, self.every_file_text())
        store.set_setting(
            SECRET_KEY, "now-just-an-ordinary-value", "alice"
        )  # not secret for this store
        self.assertEqual(
            json.loads(self.raw())["settings"][SECRET_KEY], "now-just-an-ordinary-value"
        )


class TestSettingsIntegration(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.yaml_path = self.dir / "config.yaml"
        self.yaml_path.write_text(yaml.safe_dump({"backup": {"remote_backup": {"bucket": "b"}}}))
        env = patch.dict(
            os.environ, {"SECRET_ROTATOR_DASHBOARD_SETTINGS": str(self.dir / "dash.json")}
        )
        env.start()
        self.addCleanup(env.stop)
        self.settings = Settings(config_path=str(self.yaml_path))

    def test_get_returns_the_decrypted_value_to_the_code_that_needs_it(self):
        self.settings.overrides.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.settings.reload()
        self.assertEqual(self.settings.get(SECRET_KEY), PLAINTEXT)
        self.assertEqual(self.settings.get("backup.remote_backup.bucket"), "b")

    def test_config_yaml_never_receives_dashboard_secrets(self):
        before = self.yaml_path.read_text()
        self.settings.overrides.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.settings.reload()
        self.settings.set("web.auth.username", "admin")
        self.settings.save()
        self.assertNotIn(PLAINTEXT, self.yaml_path.read_text())
        self.assertNotIn(
            "secret_access_key",
            yaml.safe_load(self.yaml_path.read_text())["backup"]["remote_backup"],
        )
        self.assertNotEqual(
            before, self.yaml_path.read_text()
        )  # only the admin username was written

    def test_base_config_holds_no_dashboard_secret(self):
        self.settings.overrides.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.settings.reload()
        self.assertNotIn(PLAINTEXT, json.dumps(self.settings.base_config))

    def test_a_fresh_process_starts_with_the_secret_available(self):
        self.settings.overrides.set_setting(SECRET_KEY, PLAINTEXT, "alice")
        self.assertEqual(Settings(config_path=str(self.yaml_path)).get(SECRET_KEY), PLAINTEXT)

    def test_reload_overrides_decrypts_too(self):
        worker = Settings(config_path=str(self.yaml_path))
        self.settings.overrides.set_setting(REDIS, "redis://:pw@cache/0", "alice")
        worker.reload_overrides()
        self.assertEqual(worker.get(REDIS), "redis://:pw@cache/0")


if __name__ == "__main__":
    unittest.main()

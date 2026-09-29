"""
Regression tests for the /api/restore provider-routing fix.

Bug (pre-1.3.1): POST /api/restore always restored the backed-up value
to ``list(engine.providers.values())[0]`` - the first registered
provider - regardless of which provider the secret actually belonged
to. With more than one provider configured, a restore would silently
write the old value into the wrong system while reporting success.

The fix teaches the endpoint to resolve the correct provider via
(in order): the ``provider_name`` recorded on the backup at creation
time, then the configured rotation job for that secret_id, then -
only as a last resort, and only for pre-upgrade backups with no other
signal - the first provider (the old behavior), logged loudly.

Covers both layers:
  - _resolve_restore_provider() directly, for every resolution branch.
  - The full HTTP endpoint, proving a restore actually lands in the
    right provider's file when more than one provider is registered.
"""

import json
import os
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from secret_rotator.backup_manager import BackupManager
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.providers.file_provider import FileSecretProvider
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter
from secret_rotator.web.routes.api import _resolve_restore_provider

TEST_USERNAME = "testadmin"
TEST_PASSWORD = "correct-horse-battery-staple"


class _FakeProvider:
    """Minimal stand-in - _resolve_restore_provider only reads .name
    and does a dict lookup, so a full FileSecretProvider isn't needed
    for the unit-level tests."""

    def __init__(self, name):
        self.name = name


class _FakeEngine:
    def __init__(self, providers, rotation_jobs=None):
        self.providers = providers
        self.rotation_jobs = rotation_jobs or []


class TestResolveRestoreProvider(unittest.TestCase):
    """Unit tests for the resolution helper itself - no Flask, no I/O."""

    def setUp(self):
        self.provider_a = _FakeProvider("postgres_prod")
        self.provider_b = _FakeProvider("mongo_atlas")
        self.engine = _FakeEngine(
            providers={"postgres_prod": self.provider_a, "mongo_atlas": self.provider_b},
            rotation_jobs=[
                {"name": "job1", "provider": "mongo_atlas", "secret_id": "mongo_svc_password"},
            ],
        )

    def test_uses_provider_name_recorded_on_backup(self):
        backup_data = {"provider_name": "mongo_atlas"}
        provider, source = _resolve_restore_provider(self.engine, backup_data, "mongo_svc_password")
        self.assertIs(provider, self.provider_b)
        self.assertEqual(source, "backup_metadata")

    def test_backup_metadata_wins_even_if_it_disagrees_with_job_config(self):
        # Deliberately contradicts the job_config for this secret_id -
        # the explicit backup record should still win.
        backup_data = {"provider_name": "postgres_prod"}
        provider, source = _resolve_restore_provider(self.engine, backup_data, "mongo_svc_password")
        self.assertIs(provider, self.provider_a)
        self.assertEqual(source, "backup_metadata")

    def test_falls_back_to_job_config_when_no_provider_name_on_backup(self):
        """Pre-1.3.1 backups have no provider_name field at all."""
        backup_data = {}
        provider, source = _resolve_restore_provider(self.engine, backup_data, "mongo_svc_password")
        self.assertIs(provider, self.provider_b)
        self.assertEqual(source, "job_config")

    def test_falls_back_to_job_config_when_named_provider_not_registered(self):
        """provider_name points at a provider that's since been
        removed from config - don't error, fall through to the next
        signal instead."""
        backup_data = {"provider_name": "no_longer_configured"}
        provider, source = _resolve_restore_provider(self.engine, backup_data, "mongo_svc_password")
        self.assertIs(provider, self.provider_b)
        self.assertEqual(source, "job_config")

    def test_falls_back_to_first_provider_when_nothing_else_matches(self):
        """No provider_name, and no job configured for this secret_id
        - this is the old (buggy) behavior, only reached as a last
        resort now, and the caller is expected to log loudly when it
        happens with more than one provider configured."""
        backup_data = {}
        provider, source = _resolve_restore_provider(self.engine, backup_data, "unknown_secret")
        self.assertIs(provider, self.provider_a)  # first in dict order
        self.assertEqual(source, "first_provider_fallback")

    def test_returns_none_when_no_providers_registered(self):
        empty_engine = _FakeEngine(providers={})
        provider, source = _resolve_restore_provider(empty_engine, {}, "any_secret")
        self.assertIsNone(provider)
        self.assertEqual(source, "no_provider")

    def test_ambiguous_job_config_falls_back_to_first_provider(self):
        """Two jobs share the same secret_id across different
        providers (unusual, but not forbidden by config) - resolving
        from job config alone would be a guess, so this correctly
        falls through to the first-provider fallback rather than
        picking one of the two silently."""
        engine = _FakeEngine(
            providers={"postgres_prod": self.provider_a, "mongo_atlas": self.provider_b},
            rotation_jobs=[
                {"name": "job1", "provider": "postgres_prod", "secret_id": "shared_id"},
                {"name": "job2", "provider": "mongo_atlas", "secret_id": "shared_id"},
            ],
        )
        provider, source = _resolve_restore_provider(engine, {}, "shared_id")
        self.assertEqual(source, "first_provider_fallback")


class TestRestoreEndpointRoutesToCorrectProvider(unittest.TestCase):
    """End-to-end: two real FileSecretProvider instances, prove a
    restore via POST /api/restore writes to the one the secret
    actually belongs to - not just whichever was registered first."""

    @classmethod
    def setUpClass(cls):
        cls._saved_env = {
            k: os.environ.pop(k, None)
            for k in (
                "SECRET_ROTATOR_ADMIN_USERNAME",
                "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
                "FLASK_SECRET_KEY",
            )
        }
        os.environ["SECRET_ROTATOR_ADMIN_USERNAME"] = TEST_USERNAME
        os.environ["SECRET_ROTATOR_ADMIN_PASSWORD_HASH"] = generate_password_hash(TEST_PASSWORD)
        os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def setUp(self):
        DistributedLock.reset_local_locks()

        # Two separate, unencrypted, file-backed providers - "first"
        # and "second" registration order matters here, since that's
        # exactly what the old bug depended on.
        self.file_a = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.file_a.write(json.dumps({"secret_x": "value_in_a"}))
        self.file_a.close()

        self.file_b = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.file_b.write(json.dumps({"secret_y": "value_in_b"}))
        self.file_b.close()

        self.temp_backup_dir = tempfile.mkdtemp()

        self.engine = RotationEngine()
        self.engine.backup_manager = BackupManager(
            backup_dir=self.temp_backup_dir, encrypt_backups=False
        )

        # provider_a registered FIRST - the old bug always picked this one.
        self.provider_a = FileSecretProvider(
            "provider_a", {"file_path": self.file_a.name, "encrypt_secrets": False}
        )
        self.provider_b = FileSecretProvider(
            "provider_b", {"file_path": self.file_b.name, "encrypt_secrets": False}
        )
        self.engine.register_provider(self.provider_a)
        self.engine.register_provider(self.provider_b)

        # secret_y belongs to provider_b - this is the fact the old
        # code ignored.
        self.engine.add_rotation_job(
            {
                "name": "job_y",
                "provider": "provider_b",
                "rotator": "unused",
                "secret_id": "secret_y",
            }
        )

        app = create_app(self.engine)
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()
        limiter.reset()
        self.client.post("/login", data={"username": TEST_USERNAME, "password": TEST_PASSWORD})

    def tearDown(self):
        for path in (self.file_a.name, self.file_b.name):
            if os.path.exists(path):
                os.unlink(path)

    def test_restore_with_provider_name_lands_in_correct_provider(self):
        """New-style backup (has provider_name) restores to provider_b,
        even though provider_a was registered first."""
        backup_path = self.engine.backup_manager.create_backup_with_checksum(
            "secret_y", "old_value_y", "new_value_y", provider_name="provider_b"
        )

        resp = self.client.post("/api/restore", json={"backup_file": backup_path})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])

        # Landed in provider_b, not provider_a.
        self.assertEqual(self.provider_b.get_secret("secret_y"), "old_value_y")
        with open(self.file_a.name) as f:
            self.assertNotIn("secret_y", json.load(f))

    def test_restore_of_pre_upgrade_backup_uses_job_config_fallback(self):
        """A backup with no provider_name (as if created before this
        fix shipped) still resolves correctly via the configured job,
        instead of silently landing in provider_a."""
        backup_path = self.engine.backup_manager.create_backup_with_checksum(
            "secret_y", "legacy_old_value", "legacy_new_value"
        )  # no provider_name passed - simulates a pre-1.3.1 backup

        resp = self.client.post("/api/restore", json={"backup_file": backup_path})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])

        self.assertEqual(self.provider_b.get_secret("secret_y"), "legacy_old_value")
        with open(self.file_a.name) as f:
            self.assertNotIn("secret_y", json.load(f))


if __name__ == "__main__":
    unittest.main()

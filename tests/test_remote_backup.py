import shutil
import tempfile
import unittest
from pathlib import Path

import boto3
from moto import mock_aws

from secret_rotator.backup_manager import BackupManager
from secret_rotator.config.settings import settings
from secret_rotator.remote_backup import RemoteBackupClient

TEST_BUCKET = "test-secret-rotator-backups"
TEST_REGION = "us-east-1"


class TestRemoteBackupClient(unittest.TestCase):
    """Exercises RemoteBackupClient against a moto-mocked S3 bucket —
    no real AWS account or network access required. moto intercepts
    boto3 calls at the botocore level, so this is the same code path
    RemoteBackupClient uses against a real bucket."""

    def setUp(self):
        self.mock = mock_aws()
        self.mock.start()

        s3 = boto3.client("s3", region_name=TEST_REGION)
        s3.create_bucket(Bucket=TEST_BUCKET)

        self.client = RemoteBackupClient(
            bucket=TEST_BUCKET, prefix="rotator/backups/", region=TEST_REGION
        )

        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.mock.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_local_file(self, name: str, content: str = "backup contents") -> Path:
        path = self.temp_dir / name
        path.write_text(content)
        return path

    def test_upload_file_succeeds(self):
        local_file = self._write_local_file("secret_a_20260101_000000.json")
        self.assertTrue(self.client.upload_file(local_file))
        self.assertIn(local_file.name, self.client.list_remote_filenames())

    def test_upload_file_to_bucket_that_does_not_exist_fails_without_raising(self):
        broken_client = RemoteBackupClient(
            bucket="this-bucket-does-not-exist-at-all", region=TEST_REGION
        )
        local_file = self._write_local_file("secret_a_20260101_000000.json")
        # Must return False, not raise — a remote backup failure must
        # never propagate up into the rotation/backup flow that
        # created the local file in the first place.
        self.assertFalse(broken_client.upload_file(local_file))

    def test_sync_directory_uploads_only_new_files(self):
        self._write_local_file("secret_a_20260101_000000.json")
        self._write_local_file("secret_b_20260101_000000.json")

        report = self.client.sync_directory(self.temp_dir, pattern="*.json")
        self.assertEqual(report["checked"], 2)
        self.assertEqual(report["uploaded"], 2)
        self.assertEqual(report["already_synced"], 0)
        self.assertEqual(report["failed"], 0)

        # A second sync with no new local files should upload nothing.
        report2 = self.client.sync_directory(self.temp_dir, pattern="*.json")
        self.assertEqual(report2["uploaded"], 0)
        self.assertEqual(report2["already_synced"], 2)

    def test_sync_directory_picks_up_a_new_file_added_later(self):
        self._write_local_file("secret_a_20260101_000000.json")
        self.client.sync_directory(self.temp_dir, pattern="*.json")

        self._write_local_file("secret_b_20260102_000000.json")
        report = self.client.sync_directory(self.temp_dir, pattern="*.json")

        self.assertEqual(report["uploaded"], 1)
        self.assertEqual(report["already_synced"], 1)

    def test_sync_directory_pattern_excludes_non_matching_files(self):
        self._write_local_file("secret_a_20260101_000000.json")
        self._write_local_file("master_key_backup.enc")

        report = self.client.sync_directory(self.temp_dir, pattern="*.json")
        self.assertEqual(report["checked"], 1)
        self.assertNotIn("master_key_backup.enc", self.client.list_remote_filenames())

    def test_subpath_places_files_under_a_nested_prefix(self):
        self._write_local_file("master_key_20260101.enc")
        self.client.sync_directory(self.temp_dir, subpath="key-backups", pattern="*.enc")

        # Visible via the subpath-aware listing...
        self.assertIn(
            "master_key_20260101.enc", self.client.list_remote_filenames(subpath="key-backups")
        )
        # ...and actually stored under a nested key (not merged into
        # the bucket-root prefix's own objects). Note that listing at
        # the bucket-root prefix *also* returns this filename — S3
        # prefix matching is a plain string prefix, not a directory
        # boundary, so "rotator/backups/" matches
        # "rotator/backups/key-backups/...", too. That's expected S3
        # behavior, not a bug; subpath's job is to keep the *stored*
        # key distinct, not to hide it from a broader listing.
        s3 = boto3.client("s3", region_name=TEST_REGION)
        keys = [obj["Key"] for obj in s3.list_objects_v2(Bucket=TEST_BUCKET).get("Contents", [])]
        self.assertIn("rotator/backups/key-backups/master_key_20260101.enc", keys)

    def test_download_file_round_trips(self):
        local_file = self._write_local_file("secret_a_20260101_000000.json", "hello world")
        self.client.upload_file(local_file)

        download_target = self.temp_dir / "downloaded.json"
        self.assertTrue(
            self.client.download_file(local_file.name, download_target)
        )
        self.assertEqual(download_target.read_text(), "hello world")

    def test_download_missing_file_fails_without_raising(self):
        target = self.temp_dir / "wont-exist.json"
        self.assertFalse(self.client.download_file("does-not-exist.json", target))


class TestRemoteBackupClientFromConfig(unittest.TestCase):
    """Covers RemoteBackupClient.from_config()'s enabled/disabled/
    misconfigured branches — the path bootstrap.py actually uses."""

    def setUp(self):
        self._snapshot = {
            key: settings.get(key)
            for key in (
                "backup.remote_backup.enabled",
                "backup.remote_backup.type",
                "backup.remote_backup.bucket",
                "backup.remote_backup.region",
                "backup.remote_backup.prefix",
                "backup.remote_backup.endpoint_url",
            )
        }

    def tearDown(self):
        for key, value in self._snapshot.items():
            settings.set(key, value)

    def test_disabled_returns_none(self):
        settings.set("backup.remote_backup.enabled", False)
        self.assertIsNone(RemoteBackupClient.from_config())

    def test_enabled_without_bucket_returns_none(self):
        settings.set("backup.remote_backup.enabled", True)
        settings.set("backup.remote_backup.bucket", None)
        self.assertIsNone(RemoteBackupClient.from_config())

    def test_unsupported_type_returns_none(self):
        settings.set("backup.remote_backup.enabled", True)
        settings.set("backup.remote_backup.bucket", TEST_BUCKET)
        settings.set("backup.remote_backup.type", "azure_blob")
        self.assertIsNone(RemoteBackupClient.from_config())

    def test_enabled_and_configured_returns_a_client(self):
        settings.set("backup.remote_backup.enabled", True)
        settings.set("backup.remote_backup.type", "s3")
        settings.set("backup.remote_backup.bucket", TEST_BUCKET)
        settings.set("backup.remote_backup.region", TEST_REGION)
        client = RemoteBackupClient.from_config()
        self.assertIsNotNone(client)
        self.assertEqual(client.bucket, TEST_BUCKET)


class TestBackupManagerRemoteWiring(unittest.TestCase):
    """Covers BackupManager's use of an injected RemoteBackupClient —
    upload_on_create and sync_to_remote() — without touching config or
    bootstrap.py directly."""

    def setUp(self):
        self.mock = mock_aws()
        self.mock.start()

        s3 = boto3.client("s3", region_name=TEST_REGION)
        s3.create_bucket(Bucket=TEST_BUCKET)

        self.remote_client = RemoteBackupClient(
            bucket=TEST_BUCKET, prefix="rotator/backups/", region=TEST_REGION
        )
        self.temp_backup_dir = tempfile.mkdtemp()

    def tearDown(self):
        self.mock.stop()
        shutil.rmtree(self.temp_backup_dir, ignore_errors=True)

    def test_no_remote_client_means_sync_to_remote_returns_none(self):
        manager = BackupManager(backup_dir=self.temp_backup_dir, encrypt_backups=False)
        self.assertIsNone(manager.remote_backup_client)
        self.assertIsNone(manager.sync_to_remote())

    def test_upload_on_create_false_does_not_upload_immediately(self):
        settings.set("backup.remote_backup.upload_on_create", False)
        manager = BackupManager(
            backup_dir=self.temp_backup_dir,
            encrypt_backups=False,
            remote_backup_client=self.remote_client,
        )
        manager.create_backup_with_checksum("secret_a", "old", "new")
        self.assertEqual(self.remote_client.list_remote_filenames(), [])

    def test_upload_on_create_true_uploads_immediately(self):
        settings.set("backup.remote_backup.upload_on_create", True)
        try:
            manager = BackupManager(
                backup_dir=self.temp_backup_dir,
                encrypt_backups=False,
                remote_backup_client=self.remote_client,
            )
            manager.create_backup_with_checksum("secret_a", "old", "new")
            self.assertEqual(len(self.remote_client.list_remote_filenames()), 1)
        finally:
            settings.set("backup.remote_backup.upload_on_create", False)

    def test_sync_to_remote_uploads_backups_created_before_remote_was_configured(self):
        # Simulates the real-world case sync_to_remote() exists for:
        # backups made while upload_on_create was off (or before
        # remote backup was enabled at all) still get caught by a
        # bulk sync.
        settings.set("backup.remote_backup.upload_on_create", False)
        manager = BackupManager(
            backup_dir=self.temp_backup_dir,
            encrypt_backups=False,
            remote_backup_client=self.remote_client,
        )
        manager.create_backup_with_checksum("secret_a", "old_a", "new_a")
        manager.create_backup_with_checksum("secret_b", "old_b", "new_b")
        self.assertEqual(self.remote_client.list_remote_filenames(), [])

        report = manager.sync_to_remote()
        self.assertEqual(report["uploaded"], 2)
        self.assertEqual(len(self.remote_client.list_remote_filenames()), 2)

    def test_remote_upload_failure_does_not_break_backup_creation(self):
        broken_client = RemoteBackupClient(
            bucket="this-bucket-does-not-exist-at-all", region=TEST_REGION
        )
        settings.set("backup.remote_backup.upload_on_create", True)
        try:
            manager = BackupManager(
                backup_dir=self.temp_backup_dir,
                encrypt_backups=False,
                remote_backup_client=broken_client,
            )
            # Must not raise despite the remote upload failing.
            backup_path = manager.create_backup_with_checksum("secret_a", "old", "new")
            self.assertTrue(Path(backup_path).exists())
        finally:
            settings.set("backup.remote_backup.upload_on_create", False)


if __name__ == "__main__":
    unittest.main()
"""
Unit tests for key_backup_manager.py.
"""

import sys
import json
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import InvalidToken

from secret_rotator.encryption_manager import EncryptionManager
from secret_rotator.key_backup_manager import MasterKeyBackupManager


class KeyBackupManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)

        self.master_key_file = Path(self.test_dir) / ".master.key"
        self.backup_dir = Path(self.test_dir) / "key_backups"

        # A real master key, created the same way the app creates one.
        EncryptionManager(key_file=str(self.master_key_file))

        self.manager = MasterKeyBackupManager(
            master_key_file=str(self.master_key_file), backup_dir=str(self.backup_dir)
        )


class TestInitialization(KeyBackupManagerTestCase):
    def test_backup_dir_created_with_owner_only_permissions(self):
        mode = stat.S_IMODE(self.backup_dir.stat().st_mode)
        self.assertEqual(mode, 0o700)


class TestEncryptedBackup(KeyBackupManagerTestCase):
    def test_missing_master_key_raises(self):
        empty_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty_dir, ignore_errors=True)
        manager = MasterKeyBackupManager(
            master_key_file=str(Path(empty_dir) / "nope.key"),
            backup_dir=str(Path(empty_dir) / "backups"),
        )
        with self.assertRaises(FileNotFoundError):
            manager.create_encrypted_key_backup(passphrase="whatever-20-chars!!")

    def test_backup_file_created_with_owner_only_permissions(self):
        backup_file = self.manager.create_encrypted_key_backup(passphrase="correct horse battery!!")
        mode = stat.S_IMODE(Path(backup_file).stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_restore_round_trip_recovers_original_key_file_content(self):
        original = json.loads(self.master_key_file.read_text())
        backup_file = self.manager.create_encrypted_key_backup(passphrase="correct horse battery!!")

        # Mutate the live key file so we can prove restore overwrites it.
        self.master_key_file.write_text(json.dumps({"key": "corrupted", "metadata": {}}))

        result = self.manager.restore_from_encrypted_backup(
            backup_file, passphrase="correct horse battery!!"
        )
        self.assertTrue(result)
        self.assertEqual(json.loads(self.master_key_file.read_text()), original)

    def test_restore_with_wrong_passphrase_raises(self):
        backup_file = self.manager.create_encrypted_key_backup(passphrase="correct horse battery!!")
        with self.assertRaises(InvalidToken):
            self.manager.restore_from_encrypted_backup(
                backup_file, passphrase="wrong passphrase!!!!"
            )

    def test_verify_only_does_not_modify_master_key_file(self):
        backup_file = self.manager.create_encrypted_key_backup(passphrase="correct horse battery!!")
        before = self.master_key_file.read_text()

        result = self.manager.restore_from_encrypted_backup(
            backup_file, passphrase="correct horse battery!!", verify_only=True
        )
        self.assertTrue(result)
        self.assertEqual(self.master_key_file.read_text(), before)

    def test_restore_creates_pre_restore_safety_copy(self):
        backup_file = self.manager.create_encrypted_key_backup(passphrase="correct horse battery!!")
        self.manager.restore_from_encrypted_backup(
            backup_file, passphrase="correct horse battery!!"
        )

        pre_restore = self.master_key_file.with_suffix(".key.pre_restore")
        self.assertTrue(pre_restore.exists())

    def test_tampered_ciphertext_fails_checksum_verification(self):
        backup_file = self.manager.create_encrypted_key_backup(passphrase="correct horse battery!!")

        with open(backup_file) as f:
            package = json.load(f)
        # Flip the last character of the encrypted payload; checksum won't match.
        package["encrypted_key_data"] = package["encrypted_key_data"][:-1] + (
            "A" if package["encrypted_key_data"][-1] != "A" else "B"
        )
        with open(backup_file, "w") as f:
            json.dump(package, f)

        with self.assertRaises(ValueError):
            self.manager.restore_from_encrypted_backup(
                backup_file, passphrase="correct horse battery!!"
            )

    def test_restore_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.manager.restore_from_encrypted_backup(
                str(Path(self.test_dir) / "nope.enc"), passphrase="x"
            )


class TestSplitKeyBackup(KeyBackupManagerTestCase):
    def test_creates_exactly_num_shares_files(self):
        shares = self.manager.create_split_key_backup(num_shares=5, threshold=3)
        self.assertEqual(len(shares), 5)
        for share in shares:
            self.assertTrue(Path(share).exists())
            self.assertEqual(stat.S_IMODE(Path(share).stat().st_mode), 0o600)

    def test_threshold_greater_than_shares_raises(self):
        with self.assertRaises(ValueError):
            self.manager.create_split_key_backup(num_shares=3, threshold=5)

    def test_missing_master_key_raises(self):
        empty_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty_dir, ignore_errors=True)
        manager = MasterKeyBackupManager(
            master_key_file=str(Path(empty_dir) / "nope.key"),
            backup_dir=str(Path(empty_dir) / "backups"),
        )
        with self.assertRaises(FileNotFoundError):
            manager.create_split_key_backup()

    def test_restore_with_exactly_threshold_shares_recovers_key(self):
        original = json.loads(self.master_key_file.read_text())
        shares = self.manager.create_split_key_backup(num_shares=5, threshold=3)

        self.master_key_file.write_text(json.dumps({"key": "corrupted", "metadata": {}}))

        result = self.manager.restore_from_split_key(shares[:3])
        self.assertTrue(result)
        self.assertEqual(json.loads(self.master_key_file.read_text()), original)

    def test_restore_with_fewer_than_threshold_shares_raises(self):
        shares = self.manager.create_split_key_backup(num_shares=5, threshold=3)
        with self.assertRaises(ValueError):
            self.manager.restore_from_split_key(shares[:2])

    def test_restore_verify_only_does_not_modify_master_key_file(self):
        shares = self.manager.create_split_key_backup(num_shares=5, threshold=3)
        before = self.master_key_file.read_text()

        result = self.manager.restore_from_split_key(shares[:3], verify_only=True)
        self.assertTrue(result)
        self.assertEqual(self.master_key_file.read_text(), before)

    def test_restore_missing_share_file_raises(self):
        shares = self.manager.create_split_key_backup(num_shares=5, threshold=3)
        shares[0] = str(Path(self.test_dir) / "does_not_exist.share")
        with self.assertRaises(FileNotFoundError):
            self.manager.restore_from_split_key(shares[:3])

    def test_restore_empty_share_list_raises(self):
        with self.assertRaises(ValueError):
            self.manager.restore_from_split_key([])

    def test_create_raises_cleanly_when_pyshamir_not_installed(self):
        sys.modules["pyshamir"] = None  # forces ImportError on `from pyshamir import split`
        self.addCleanup(sys.modules.pop, "pyshamir", None)
        with self.assertRaises(ImportError):
            self.manager.create_split_key_backup()

    def test_restore_raises_cleanly_when_pyshamir_not_installed(self):
        shares = self.manager.create_split_key_backup(num_shares=3, threshold=2)
        sys.modules["pyshamir"] = None
        self.addCleanup(sys.modules.pop, "pyshamir", None)
        with self.assertRaises(ImportError):
            self.manager.restore_from_split_key(shares[:2])


class TestPlaintextBackup(KeyBackupManagerTestCase):
    def test_creates_file_with_owner_only_permissions(self):
        backup_file = self.manager.create_plaintext_backup()
        mode = stat.S_IMODE(Path(backup_file).stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_content_matches_master_key_file(self):
        backup_file = self.manager.create_plaintext_backup()
        self.assertEqual(Path(backup_file).read_text(), self.master_key_file.read_text())

    def test_missing_master_key_raises(self):
        empty_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty_dir, ignore_errors=True)
        manager = MasterKeyBackupManager(
            master_key_file=str(Path(empty_dir) / "nope.key"),
            backup_dir=str(Path(empty_dir) / "backups"),
        )
        with self.assertRaises(FileNotFoundError):
            manager.create_plaintext_backup()


class TestListAndVerifyBackups(KeyBackupManagerTestCase):
    def test_list_backups_includes_encrypted_split_and_plaintext(self):
        self.manager.create_encrypted_key_backup(passphrase="correct horse battery!!")
        self.manager.create_split_key_backup(num_shares=3, threshold=2)
        self.manager.create_plaintext_backup()

        backups = self.manager.list_backups()
        types = {b["type"] for b in backups}
        self.assertEqual(types, {"encrypted", "split_key", "plaintext"})

    def test_split_key_group_reports_complete_status(self):
        """Regression test: create_split_key_backup() must stamp every
        share in one batch with the *same* created_at, or list_backups()'s
        grouping-by-created_at silently splits them into N one-share
        groups that never reach 'complete' status."""
        self.manager.create_split_key_backup(num_shares=4, threshold=3)
        backups = self.manager.list_backups()
        split = next(b for b in backups if b["type"] == "split_key")
        self.assertEqual(split["available_shares"], 4)
        self.assertEqual(split["status"], "complete")

    def test_two_separate_split_backups_are_not_merged_into_one_group(self):
        self.manager.create_split_key_backup(num_shares=3, threshold=2)
        self.manager.create_split_key_backup(num_shares=3, threshold=2)

        backups = self.manager.list_backups()
        groups = [b for b in backups if b["type"] == "split_key"]
        self.assertEqual(len(groups), 2)
        for group in groups:
            self.assertEqual(group["available_shares"], 3)

    def test_verify_encrypted_backup_requires_passphrase(self):
        backup_file = self.manager.create_encrypted_key_backup(passphrase="correct horse battery!!")
        self.assertFalse(self.manager.verify_backup(backup_file))

    def test_verify_encrypted_backup_with_correct_passphrase(self):
        backup_file = self.manager.create_encrypted_key_backup(passphrase="correct horse battery!!")
        self.assertTrue(
            self.manager.verify_backup(backup_file, passphrase="correct horse battery!!")
        )

    def test_verify_plaintext_backup(self):
        backup_file = self.manager.create_plaintext_backup()
        self.assertTrue(self.manager.verify_backup(backup_file))

    def test_verify_single_share_cannot_be_verified_alone(self):
        shares = self.manager.create_split_key_backup(num_shares=3, threshold=2)
        self.assertFalse(self.manager.verify_backup(shares[0]))

    def test_verify_missing_file_returns_false(self):
        self.assertFalse(self.manager.verify_backup(str(Path(self.test_dir) / "missing.enc")))

    def test_verify_unknown_extension_returns_false(self):
        unknown = Path(self.test_dir) / "backup.unknown"
        unknown.write_text("data")
        self.assertFalse(self.manager.verify_backup(str(unknown)))


class TestExportBackupInstructions(KeyBackupManagerTestCase):
    def test_writes_a_file_with_recovery_guidance(self):
        output_file = Path(self.test_dir) / "INSTRUCTIONS.txt"
        result_path = self.manager.export_backup_instructions(output_file=str(output_file))

        self.assertEqual(result_path, str(output_file))
        content = output_file.read_text()
        self.assertIn("RECOVERY PROCEDURES", content)
        self.assertIn(str(self.backup_dir), content)


if __name__ == "__main__":
    unittest.main()

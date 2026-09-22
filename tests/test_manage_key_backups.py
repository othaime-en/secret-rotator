"""
Unit tests for tools/manage_key_backups.py - the `secret-rotator-backup`
CLI.

Covers:
  - argument parser wiring for every subcommand (defaults, required
    args, flags)
  - create_encrypted_backup / create_split_backup / create_plaintext_backup:
    "no" at the confirmation prompt cancels without creating anything;
    "yes" creates a backup; passphrase-too-short is rejected
  - list_backups / verify_backup / restore_backup / restore_split_backup:
    happy path and the key failure paths (missing file, wrong
    passphrase, non-.enc file rejected for restore)
  - export_instructions delegates to the manager

Each test builds an argparse.Namespace directly and calls the command
function, rather than going through sys.argv, except for the parser
tests which exercise main()'s parser directly.
"""

import shutil
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from secret_rotator.encryption_manager import EncryptionManager
from secret_rotator.key_backup_manager import MasterKeyBackupManager
from secret_rotator.tools import manage_key_backups as cli


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)

        self.key_file = Path(self.test_dir) / ".master.key"
        self.backup_dir = Path(self.test_dir) / "key_backups"
        EncryptionManager(key_file=str(self.key_file))  # real master key

    def _ns(self, **kwargs):
        base = {"key_file": str(self.key_file), "backup_dir": str(self.backup_dir)}
        base.update(kwargs)
        return Namespace(**base)


class TestArgumentParser(unittest.TestCase):
    """Drives the real parser built inside cli.main(), with every command
    handler replaced by a stub that just captures the parsed Namespace —
    so these tests check argument wiring, not command behavior."""

    def _parse(self, argv):
        captured = {}

        def fake_command(args):
            captured["args"] = args

        handler_names = [
            "create_encrypted_backup",
            "create_split_backup",
            "create_plaintext_backup",
            "list_backups",
            "verify_backup",
            "restore_backup",
            "restore_split_backup",
            "export_instructions",
            "sync_remote",
        ]
        patches = [mock.patch.object(cli, name, fake_command) for name in handler_names]

        with mock.patch("sys.argv", ["secret-rotator-backup"] + argv):
            for p in patches:
                p.start()
            try:
                cli.main()
            finally:
                for p in patches:
                    p.stop()

        return captured["args"]

    def test_create_encrypted_defaults(self):
        args = self._parse(["create-encrypted"])
        self.assertEqual(args.key_file, "data/.master.key")
        self.assertEqual(args.backup_dir, "data/key_backups")
        self.assertIsNone(args.name)
        self.assertIsNone(args.passphrase_file)

    def test_create_split_defaults(self):
        args = self._parse(["create-split"])
        self.assertEqual(args.shares, 5)
        self.assertEqual(args.threshold, 3)

    def test_create_split_custom_values(self):
        args = self._parse(["create-split", "--shares", "7", "--threshold", "4"])
        self.assertEqual(args.shares, 7)
        self.assertEqual(args.threshold, 4)

    def test_verify_requires_backup_file_positional(self):
        args = self._parse(["verify", "mybackup.enc"])
        self.assertEqual(args.backup_file, "mybackup.enc")

    def test_restore_split_accepts_multiple_share_files(self):
        args = self._parse(["restore-split", "a.share", "b.share", "c.share"])
        self.assertEqual(args.share_files, ["a.share", "b.share", "c.share"])

    def test_export_instructions_default_output(self):
        args = self._parse(["export-instructions"])
        self.assertEqual(args.output, "KEY_BACKUP_INSTRUCTIONS.txt")

    def test_no_command_prints_help_and_exits_nonzero(self):
        with mock.patch("sys.argv", ["secret-rotator-backup"]):
            with self.assertRaises(SystemExit) as cm:
                cli.main()
        self.assertNotEqual(cm.exception.code, 0)


class TestCreateEncryptedBackup(CliTestCase):
    def test_cli_passphrase_file_too_short_is_rejected(self):
        short_pass_file = Path(self.test_dir) / "short.txt"
        short_pass_file.write_text("short")
        args = self._ns(passphrase_file=str(short_pass_file), name=None)

        with self.assertRaises(SystemExit):
            cli.create_encrypted_backup(args)

    def test_cli_passphrase_file_creates_backup(self):
        pass_file = Path(self.test_dir) / "pass.txt"
        pass_file.write_text("a-strong-passphrase-20chars")
        args = self._ns(passphrase_file=str(pass_file), name="mybackup")

        cli.create_encrypted_backup(args)

        manager = MasterKeyBackupManager(
            master_key_file=str(self.key_file), backup_dir=str(self.backup_dir)
        )
        backups = manager.list_backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["type"], "encrypted")


class TestCreateSplitBackup(CliTestCase):
    def test_declining_confirmation_creates_no_shares(self):
        args = self._ns(shares=5, threshold=3)
        with mock.patch("builtins.input", return_value="no"):
            cli.create_split_backup(args)
        self.assertFalse(self.backup_dir.exists() and any(self.backup_dir.glob("*.share")))

    def test_confirming_creates_shares(self):
        args = self._ns(shares=4, threshold=2)
        with mock.patch("builtins.input", return_value="yes"):
            cli.create_split_backup(args)
        self.assertEqual(len(list(self.backup_dir.glob("*.share"))), 4)


class TestCreatePlaintextBackup(CliTestCase):
    def test_declining_confirmation_creates_no_backup(self):
        args = self._ns(name=None)
        with mock.patch("builtins.input", return_value="no"):
            cli.create_plaintext_backup(args)
        self.assertFalse(self.backup_dir.exists() and any(self.backup_dir.glob("*.key")))

    def test_confirming_creates_backup(self):
        args = self._ns(name=None)
        with mock.patch("builtins.input", return_value="yes"):
            cli.create_plaintext_backup(args)
        self.assertEqual(len(list(self.backup_dir.glob("*.key"))), 1)


class TestListVerifyRestore(CliTestCase):
    def _manager(self):
        return MasterKeyBackupManager(
            master_key_file=str(self.key_file), backup_dir=str(self.backup_dir)
        )

    def test_list_with_no_backups_does_not_raise(self):
        args = self._ns()
        cli.list_backups(args)  # should just print "No backups found."

    def test_list_with_backups_does_not_raise(self):
        self._manager().create_plaintext_backup()
        cli.list_backups(self._ns())

    def test_verify_plaintext_backup_success(self):
        backup_file = self._manager().create_plaintext_backup()
        args = self._ns(backup_file=backup_file, passphrase_file=None)
        cli.verify_backup(args)  # no SystemExit raised => success path

    def test_verify_missing_backup_exits_nonzero(self):
        args = self._ns(
            backup_file=str(Path(self.test_dir) / "nope.key"), passphrase_file=None
        )
        with self.assertRaises(SystemExit):
            cli.verify_backup(args)

    def test_verify_encrypted_with_passphrase_file(self):
        backup_file = self._manager().create_encrypted_key_backup(passphrase="correct horse battery!!")
        pass_file = Path(self.test_dir) / "pass.txt"
        pass_file.write_text("correct horse battery!!")

        args = self._ns(backup_file=backup_file, passphrase_file=str(pass_file))
        cli.verify_backup(args)  # no SystemExit => verified successfully

    def test_verify_encrypted_with_wrong_passphrase_exits_nonzero(self):
        backup_file = self._manager().create_encrypted_key_backup(passphrase="correct horse battery!!")
        pass_file = Path(self.test_dir) / "wrong.txt"
        pass_file.write_text("totally-wrong-passphrase")

        args = self._ns(backup_file=backup_file, passphrase_file=str(pass_file))
        with self.assertRaises(SystemExit):
            cli.verify_backup(args)

    def test_restore_declining_confirmation_does_not_restore(self):
        backup_file = self._manager().create_encrypted_key_backup(passphrase="correct horse battery!!")
        before = self.key_file.read_text()

        args = self._ns(backup_file=backup_file, passphrase_file=None)
        with mock.patch("builtins.input", return_value="no"):
            cli.restore_backup(args)

        self.assertEqual(self.key_file.read_text(), before)

    def test_restore_non_enc_file_rejected(self):
        backup_file = self._manager().create_plaintext_backup()
        args = self._ns(backup_file=backup_file, passphrase_file=None)
        with mock.patch("builtins.input", return_value="yes"):
            with self.assertRaises(SystemExit):
                cli.restore_backup(args)

    def test_restore_split_missing_share_file_exits_before_prompting(self):
        args = self._ns(share_files=[str(Path(self.test_dir) / "missing.share")])
        with self.assertRaises(SystemExit):
            cli.restore_split_backup(args)

    def test_restore_split_success(self):
        shares = self._manager().create_split_key_backup(num_shares=3, threshold=2)
        args = self._ns(share_files=shares[:2])
        with mock.patch("builtins.input", return_value="yes"):
            cli.restore_split_backup(args)  # no SystemExit => success


class TestExportInstructions(CliTestCase):
    def test_writes_instructions_file(self):
        output = Path(self.test_dir) / "INSTRUCTIONS.txt"
        args = self._ns(output=str(output))
        cli.export_instructions(args)
        self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
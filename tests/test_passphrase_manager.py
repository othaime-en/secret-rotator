"""
Unit tests for utils/passphrase_manager.py.

Covers the full priority chain (CLI file > config >
standard locations > env var > stdin > interactive), file
permission handling, and the help-message / interactive-prompt paths.

STANDARD_LOCATIONS is patched to point into a temp directory for every
test in this file so we never accidentally read a real file from the
machine running the tests (e.g. an actual ~/.config/secret-rotator/
passphrase file).
"""

import io
import os
import stat
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from secret_rotator.utils.passphrase_manager import PassphraseManager


class PassphraseManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)

        # Never let a test accidentally hit a real path on the host.
        self._locations_patch = mock.patch.object(
            PassphraseManager,
            "STANDARD_LOCATIONS",
            [str(Path(self.test_dir) / "loc1"), str(Path(self.test_dir) / "loc2")],
        )
        self._locations_patch.start()
        self.addCleanup(self._locations_patch.stop)

        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        os.environ.pop("BACKUP_PASSPHRASE", None)


class TestGetPassphrasePriorityChain(PassphraseManagerTestCase):
    def test_cli_file_takes_priority_over_everything_else(self):
        cli_file = Path(self.test_dir) / "cli.txt"
        cli_file.write_text("cli-passphrase")
        os.environ["BACKUP_PASSPHRASE"] = "env-passphrase"

        pm = PassphraseManager()
        passphrase, source = pm.get_passphrase(cli_file=str(cli_file))
        self.assertEqual(passphrase, "cli-passphrase")
        self.assertIn("CLI argument", source)

    def test_cli_file_missing_returns_none_with_reason(self):
        pm = PassphraseManager()
        passphrase, source = pm.get_passphrase(cli_file=str(Path(self.test_dir) / "missing.txt"))
        self.assertIsNone(passphrase)
        self.assertIn("CLI file not found", source)

    def test_config_source_takes_priority_over_standard_locations_and_env(self):
        Path(self.test_dir, "loc1").write_text("standard-location-passphrase")
        os.environ["BACKUP_PASSPHRASE"] = "env-passphrase"

        fake_config = mock.Mock()
        fake_config.get.return_value = "env:BACKUP_PASSPHRASE"
        # config source resolves to the env var, but the point of this
        # test is that get_from_config() is consulted before standard
        # locations, not which value wins in the end.
        pm = PassphraseManager(config_manager=fake_config)
        passphrase, source = pm.get_passphrase()
        self.assertEqual(passphrase, "env-passphrase")
        self.assertIn("config env variable", source)

    def test_standard_location_used_when_no_cli_or_config(self):
        Path(self.test_dir, "loc2").write_text("  standard-passphrase  \n")
        pm = PassphraseManager()
        passphrase, source = pm.get_passphrase()
        self.assertEqual(passphrase, "standard-passphrase")
        self.assertIn("standard location", source)

    def test_env_var_used_when_no_higher_priority_source(self):
        os.environ["BACKUP_PASSPHRASE"] = "env-only-passphrase"
        pm = PassphraseManager()
        passphrase, source = pm.get_passphrase()
        self.assertEqual(passphrase, "env-only-passphrase")
        self.assertEqual(source, "BACKUP_PASSPHRASE environment variable")

    def test_stdin_used_when_piped_and_no_other_source(self):
        pm = PassphraseManager()
        with mock.patch("sys.stdin", io.StringIO("piped-passphrase\n")):
            with mock.patch("sys.stdin.isatty", return_value=False):
                passphrase, source = pm.get_passphrase()
        self.assertEqual(passphrase, "piped-passphrase")
        self.assertEqual(source, "stdin")

    def test_non_interactive_with_nothing_available_reports_reason(self):
        pm = PassphraseManager()
        with mock.patch("sys.stdin", io.StringIO("")):
            with mock.patch("sys.stdin.isatty", return_value=False):
                passphrase, source = pm.get_passphrase()
        self.assertIsNone(passphrase)
        self.assertEqual(source, "non_interactive_no_source")

    def test_interactive_required_when_tty_and_no_other_source(self):
        pm = PassphraseManager()
        with mock.patch("sys.stdin.isatty", return_value=True):
            passphrase, source = pm.get_passphrase(allow_interactive=True)
        self.assertIsNone(passphrase)
        self.assertEqual(source, "interactive_required")

    def test_interactive_disallowed_reports_no_source_available(self):
        pm = PassphraseManager()
        with mock.patch("sys.stdin.isatty", return_value=True):
            passphrase, source = pm.get_passphrase(allow_interactive=False)
        self.assertIsNone(passphrase)
        self.assertEqual(source, "no_source_available")


class TestReadFromFile(PassphraseManagerTestCase):
    def test_empty_file_treated_as_no_passphrase(self):
        empty_file = Path(self.test_dir) / "empty.txt"
        empty_file.write_text("   \n")
        pm = PassphraseManager()
        self.assertIsNone(pm._read_from_file(str(empty_file)))

    def test_whitespace_is_stripped(self):
        f = Path(self.test_dir) / "padded.txt"
        f.write_text("  my-passphrase  \n")
        pm = PassphraseManager()
        self.assertEqual(pm._read_from_file(str(f)), "my-passphrase")

    def test_nonexistent_file_returns_none(self):
        pm = PassphraseManager()
        self.assertIsNone(pm._read_from_file(str(Path(self.test_dir) / "nope.txt")))

    def test_directory_path_returns_none_rather_than_raising(self):
        pm = PassphraseManager()
        self.assertIsNone(pm._read_from_file(self.test_dir))


class TestGetFromConfig(PassphraseManagerTestCase):
    def test_no_config_source_returns_none(self):
        fake_config = mock.Mock()
        fake_config.get.return_value = None
        pm = PassphraseManager(config_manager=fake_config)
        passphrase, source = pm._get_from_config()
        self.assertIsNone(passphrase)

    def test_file_source_reads_the_configured_file(self):
        f = Path(self.test_dir) / "configured.txt"
        f.write_text("configured-passphrase")
        fake_config = mock.Mock()
        fake_config.get.return_value = f"file:{f}"
        pm = PassphraseManager(config_manager=fake_config)
        passphrase, source = pm._get_from_config()
        self.assertEqual(passphrase, "configured-passphrase")
        self.assertIn("config file source", source)

    def test_env_source_reads_the_configured_env_var(self):
        os.environ["MY_CUSTOM_PASSPHRASE_VAR"] = "custom-env-passphrase"
        fake_config = mock.Mock()
        fake_config.get.return_value = "env:MY_CUSTOM_PASSPHRASE_VAR"
        pm = PassphraseManager(config_manager=fake_config)
        passphrase, source = pm._get_from_config()
        self.assertEqual(passphrase, "custom-env-passphrase")


class TestPromptInteractive(PassphraseManagerTestCase):
    def test_returns_passphrase_when_confirmation_matches(self):
        pm = PassphraseManager()
        with mock.patch(
            "secret_rotator.utils.passphrase_manager.getpass.getpass",
            side_effect=["a-strong-passphrase-20chars", "a-strong-passphrase-20chars"],
        ):
            result = pm.prompt_interactive(min_length=20, require_confirmation=True)
        self.assertEqual(result, "a-strong-passphrase-20chars")

    def test_mismatched_confirmation_reprompts_until_matched(self):
        pm = PassphraseManager()
        with mock.patch(
            "secret_rotator.utils.passphrase_manager.getpass.getpass",
            side_effect=[
                "first-entry-2030chars",
                "does-not-match",
                "second-try-20-chars",
                "second-try-20-chars",
            ],
        ):
            result = pm.prompt_interactive(min_length=5, require_confirmation=True)
        self.assertEqual(result, "second-try-20-chars")

    def test_empty_passphrase_is_rejected_and_reprompted(self):
        pm = PassphraseManager()
        with mock.patch(
            "secret_rotator.utils.passphrase_manager.getpass.getpass",
            side_effect=["", "final-passphrase", "final-passphrase"],
        ):
            result = pm.prompt_interactive(min_length=5, require_confirmation=True)
        self.assertEqual(result, "final-passphrase")

    def test_short_passphrase_prompts_continue_and_accepts_yes(self):
        pm = PassphraseManager()
        with mock.patch(
            "secret_rotator.utils.passphrase_manager.getpass.getpass",
            side_effect=["short", "short"],
        ):
            with mock.patch("builtins.input", return_value="yes"):
                result = pm.prompt_interactive(min_length=20, require_confirmation=True)
        self.assertEqual(result, "short")

    def test_no_confirmation_required_returns_immediately(self):
        pm = PassphraseManager()
        with mock.patch(
            "secret_rotator.utils.passphrase_manager.getpass.getpass",
            return_value="a-strong-passphrase-20chars",
        ):
            result = pm.prompt_interactive(min_length=5, require_confirmation=False)
        self.assertEqual(result, "a-strong-passphrase-20chars")


class TestCreatePassphraseFile(PassphraseManagerTestCase):
    def test_writes_file_with_owner_only_permissions(self):
        target = Path(self.test_dir) / "out" / ".backup-passphrase"
        pm = PassphraseManager()
        result = pm.create_passphrase_file(str(target), passphrase="explicit-passphrase")
        self.assertTrue(result)
        self.assertEqual(target.read_text(), "explicit-passphrase")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_creates_parent_directories(self):
        target = Path(self.test_dir) / "deep" / "nested" / "dir" / ".backup-passphrase"
        pm = PassphraseManager()
        self.assertTrue(pm.create_passphrase_file(str(target), passphrase="x"))
        self.assertTrue(target.exists())

    def test_no_passphrase_and_non_interactive_returns_false(self):
        target = Path(self.test_dir) / ".backup-passphrase"
        pm = PassphraseManager()
        result = pm.create_passphrase_file(str(target), passphrase=None, interactive=False)
        self.assertFalse(result)
        self.assertFalse(target.exists())

    def test_interactive_prompt_used_when_no_passphrase_given(self):
        target = Path(self.test_dir) / ".backup-passphrase"
        pm = PassphraseManager()
        with mock.patch.object(pm, "prompt_interactive", return_value="prompted-passphrase"):
            result = pm.create_passphrase_file(str(target), passphrase=None, interactive=True)
        self.assertTrue(result)
        self.assertEqual(target.read_text(), "prompted-passphrase")

    def test_keyboard_interrupt_during_prompt_returns_false(self):
        target = Path(self.test_dir) / ".backup-passphrase"
        pm = PassphraseManager()
        with mock.patch.object(pm, "prompt_interactive", side_effect=KeyboardInterrupt):
            result = pm.create_passphrase_file(str(target), passphrase=None, interactive=True)
        self.assertFalse(result)
        self.assertFalse(target.exists())


class TestPrintHelpMessage(PassphraseManagerTestCase):
    def test_docker_hint_shown_when_is_docker_true(self):
        pm = PassphraseManager()
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            pm.print_help_message(is_docker=True)
        self.assertIn("docker exec", buf.getvalue())

    def test_pypi_hint_shown_when_is_docker_false(self):
        pm = PassphraseManager()
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            pm.print_help_message(is_docker=False)
        self.assertIn("~/.config/secret-rotator", buf.getvalue())
        self.assertNotIn("docker exec", buf.getvalue())

    def test_always_mentions_environment_variable_option(self):
        pm = PassphraseManager()
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            pm.print_help_message(is_docker=False)
        self.assertIn("BACKUP_PASSPHRASE", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

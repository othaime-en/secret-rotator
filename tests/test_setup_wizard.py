"""
Unit tests for setup_wizard.py.
"""

import os
import stat
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from secret_rotator import setup_wizard


class SetupWizardTestCase(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)
        self.config_dir = Path(self.test_dir) / "config"
        self.data_dir = Path(self.test_dir) / "data"
        self.log_dir = Path(self.test_dir) / "logs"


class TestDirectoryResolution(unittest.TestCase):
    def setUp(self):
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            os.environ.pop(var, None)

    def test_config_dir_respects_xdg_config_home(self):
        with mock.patch("sys.platform", "linux"):
            os.environ["XDG_CONFIG_HOME"] = "/custom/xdg/config"
            self.assertEqual(
                setup_wizard.get_config_dir(), Path("/custom/xdg/config/secret-rotator")
            )

    def test_config_dir_falls_back_to_home_when_no_xdg(self):
        with mock.patch("sys.platform", "linux"):
            self.assertEqual(
                setup_wizard.get_config_dir(), Path.home() / ".config" / "secret-rotator"
            )

    def test_data_dir_respects_xdg_data_home(self):
        with mock.patch("sys.platform", "linux"):
            os.environ["XDG_DATA_HOME"] = "/custom/xdg/data"
            self.assertEqual(setup_wizard.get_data_dir(), Path("/custom/xdg/data/secret-rotator"))

    def test_data_dir_falls_back_to_home_when_no_xdg(self):
        with mock.patch("sys.platform", "linux"):
            self.assertEqual(
                setup_wizard.get_data_dir(), Path.home() / ".local" / "share" / "secret-rotator"
            )

    def test_log_dir_respects_xdg_state_home(self):
        with mock.patch("sys.platform", "linux"):
            os.environ["XDG_STATE_HOME"] = "/custom/xdg/state"
            self.assertEqual(
                setup_wizard.get_log_dir(), Path("/custom/xdg/state/secret-rotator/logs")
            )

    def test_windows_config_dir_uses_appdata(self):
        with mock.patch("sys.platform", "win32"):
            os.environ["APPDATA"] = r"C:\Users\tester\AppData\Roaming"
            self.assertEqual(
                setup_wizard.get_config_dir(),
                Path(r"C:\Users\tester\AppData\Roaming") / "secret-rotator",
            )


class TestCreateDirectories(SetupWizardTestCase):
    def test_all_directories_created_with_owner_only_permissions(self):
        setup_wizard.create_directories(self.config_dir, self.data_dir, self.log_dir)

        for directory in (self.config_dir, self.data_dir, self.data_dir / "backup", self.log_dir):
            self.assertTrue(directory.is_dir())
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)


class TestCreateConfig(SetupWizardTestCase):
    def setUp(self):
        super().setUp()
        for d in (self.config_dir, self.data_dir, self.log_dir):
            d.mkdir(parents=True)

    def test_writes_valid_yaml_with_expected_top_level_sections(self):
        with mock.patch("builtins.input", return_value="1"):
            config_file = setup_wizard.create_config(self.config_dir, self.data_dir, self.log_dir)

        self.assertTrue(config_file.exists())
        with open(config_file) as f:
            config = yaml.safe_load(f)

        for section in (
            "rotation",
            "logging",
            "web",
            "providers",
            "rotators",
            "security",
            "backup",
            "jobs",
        ):
            self.assertIn(section, config)
        self.assertEqual(config["rotation"]["schedule"], "daily")

    def test_config_file_has_owner_only_permissions(self):
        with mock.patch("builtins.input", return_value="1"):
            config_file = setup_wizard.create_config(self.config_dir, self.data_dir, self.log_dir)
        self.assertEqual(stat.S_IMODE(config_file.stat().st_mode), 0o600)

    def test_schedule_choice_2_selects_weekly(self):
        with mock.patch("builtins.input", return_value="2"):
            config_file = setup_wizard.create_config(self.config_dir, self.data_dir, self.log_dir)
        config = yaml.safe_load(config_file.read_text())
        self.assertEqual(config["rotation"]["schedule"], "weekly")

    def test_default_choice_when_input_is_blank(self):
        with mock.patch("builtins.input", return_value=""):
            config_file = setup_wizard.create_config(self.config_dir, self.data_dir, self.log_dir)
        config = yaml.safe_load(config_file.read_text())
        self.assertEqual(config["rotation"]["schedule"], "daily")

    def test_picking_option_1_does_not_prompt_a_second_time(self):
        """Regression test: create_config() must only call input() once
        (for the schedule choice itself) when the user picks 1/2/3 — it
        must not also eagerly ask for a custom schedule and discard it."""
        with mock.patch("builtins.input", return_value="1") as mocked_input:
            setup_wizard.create_config(self.config_dir, self.data_dir, self.log_dir)
        self.assertEqual(mocked_input.call_count, 1)

    def test_choice_4_prompts_for_and_uses_custom_schedule(self):
        with mock.patch("builtins.input", side_effect=["4", "every_45_minutes"]):
            config_file = setup_wizard.create_config(self.config_dir, self.data_dir, self.log_dir)
        config = yaml.safe_load(config_file.read_text())
        self.assertEqual(config["rotation"]["schedule"], "every_45_minutes")

    def test_existing_config_not_overwritten_when_user_declines(self):
        config_file = self.config_dir / "config.yaml"
        config_file.write_text("original: true\n")

        with mock.patch("builtins.input", return_value="no"):
            result = setup_wizard.create_config(self.config_dir, self.data_dir, self.log_dir)

        self.assertEqual(result, config_file)
        self.assertEqual(yaml.safe_load(config_file.read_text()), {"original": True})

    def test_existing_config_overwritten_when_user_confirms(self):
        config_file = self.config_dir / "config.yaml"
        config_file.write_text("original: true\n")

        with mock.patch("builtins.input", side_effect=["yes", "1"]):
            setup_wizard.create_config(self.config_dir, self.data_dir, self.log_dir)

        config = yaml.safe_load(config_file.read_text())
        self.assertIn("rotation", config)
        self.assertNotIn("original", config)


class TestSetupEncryption(SetupWizardTestCase):
    def setUp(self):
        super().setUp()
        self.config_dir.mkdir(parents=True)

    def test_generates_master_key_with_owner_only_permissions(self):
        setup_wizard.setup_encryption(self.config_dir)
        key_file = self.config_dir / ".master.key"
        self.assertTrue(key_file.exists())
        self.assertEqual(stat.S_IMODE(key_file.stat().st_mode), 0o600)

    def test_existing_key_kept_when_user_declines_regeneration(self):
        key_file = self.config_dir / ".master.key"
        setup_wizard.setup_encryption(self.config_dir)  # first-run, no prompt
        original_content = key_file.read_text()

        with mock.patch("builtins.input", return_value="no"):
            setup_wizard.setup_encryption(self.config_dir)

        self.assertEqual(key_file.read_text(), original_content)

    def test_existing_key_backed_up_before_regeneration(self):
        key_file = self.config_dir / ".master.key"
        setup_wizard.setup_encryption(self.config_dir)
        original_content = key_file.read_text()

        with mock.patch("builtins.input", return_value="yes"):
            setup_wizard.setup_encryption(self.config_dir)

        backup_file = key_file.with_suffix(".key.backup")
        self.assertTrue(backup_file.exists())
        self.assertEqual(backup_file.read_text(), original_content)


class TestSetupBackupPassphrase(SetupWizardTestCase):
    def setUp(self):
        super().setUp()
        for d in (self.config_dir, self.data_dir):
            d.mkdir(parents=True)

    def test_choice_1_selects_interactive(self):
        with mock.patch("builtins.input", return_value="1"):
            result = setup_wizard.setup_backup_passphrase(self.config_dir, self.data_dir)
        self.assertEqual(result, "interactive")

    def test_choice_3_selects_custom_env_var(self):
        with mock.patch("builtins.input", side_effect=["3", "MY_PASSPHRASE_VAR"]):
            result = setup_wizard.setup_backup_passphrase(self.config_dir, self.data_dir)
        self.assertEqual(result, "env:MY_PASSPHRASE_VAR")

    def test_choice_3_defaults_env_var_name_when_blank(self):
        with mock.patch("builtins.input", side_effect=["3", ""]):
            result = setup_wizard.setup_backup_passphrase(self.config_dir, self.data_dir)
        self.assertEqual(result, "env:BACKUP_PASSPHRASE")

    def test_choice_4_defers_configuration(self):
        with mock.patch("builtins.input", return_value="4"):
            result = setup_wizard.setup_backup_passphrase(self.config_dir, self.data_dir)
        self.assertEqual(result, "interactive")

    def test_invalid_choice_falls_back_to_interactive(self):
        with mock.patch("builtins.input", return_value="9"):
            result = setup_wizard.setup_backup_passphrase(self.config_dir, self.data_dir)
        self.assertEqual(result, "interactive")

    def test_choice_2_creates_passphrase_file_and_returns_file_source(self):
        with mock.patch("builtins.input", return_value="2"):
            with mock.patch(
                "secret_rotator.setup_wizard.PassphraseManager.create_passphrase_file",
                return_value=True,
            ):
                result = setup_wizard.setup_backup_passphrase(self.config_dir, self.data_dir)
        self.assertTrue(result.startswith("file:"))

    def test_choice_2_falls_back_to_interactive_on_failure(self):
        with mock.patch("builtins.input", return_value="2"):
            with mock.patch(
                "secret_rotator.setup_wizard.PassphraseManager.create_passphrase_file",
                return_value=False,
            ):
                result = setup_wizard.setup_backup_passphrase(self.config_dir, self.data_dir)
        self.assertEqual(result, "interactive")


class TestMainEntryPoint(SetupWizardTestCase):
    def test_declining_initial_confirmation_exits_without_creating_anything(self):
        with (
            mock.patch("secret_rotator.setup_wizard.get_config_dir", return_value=self.config_dir),
            mock.patch("secret_rotator.setup_wizard.get_data_dir", return_value=self.data_dir),
            mock.patch("secret_rotator.setup_wizard.get_log_dir", return_value=self.log_dir),
            mock.patch("builtins.input", return_value="no"),
        ):
            with self.assertRaises(SystemExit) as cm:
                setup_wizard.main()

        self.assertEqual(cm.exception.code, 0)
        self.assertFalse(self.config_dir.exists())


if __name__ == "__main__":
    unittest.main()

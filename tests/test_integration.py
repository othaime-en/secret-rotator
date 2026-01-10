import unittest
import tempfile
import sys
import os
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.backup_manager import BackupManager
from secret_rotator.providers.file_provider import FileSecretProvider
from secret_rotator.rotators.password_rotator import PasswordRotator


class TestIntegrationWithEncryption(unittest.TestCase):
    """Integration tests for complete rotation workflow with encryption"""

    def setUp(self):
        """Set up test fixtures"""
        self.temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.temp_file.write("{}")
        self.temp_file.close()

        self.temp_key_file = tempfile.NamedTemporaryFile(mode="wb", suffix=".key", delete=False)

        # Generate a valid Fernet key for testing
        test_key = Fernet.generate_key()
        self.temp_key_file.write(test_key)
        self.temp_key_file.close()

        # Set restrictive permissions on key file (just like the real system does)
        os.chmod(self.temp_key_file.name, 0o600)

        self.temp_backup_dir = tempfile.mkdtemp()

        self.engine = RotationEngine()
        self.engine.backup_manager = BackupManager(
            backup_dir=self.temp_backup_dir, encrypt_backups=True
        )

        # Set up provider with encryption
        provider = FileSecretProvider(
            "file_storage",
            {
                "file_path": self.temp_file.name,
                "encrypt_secrets": True,
                "encryption_key_file": self.temp_key_file.name,
            },
        )
        self.engine.register_provider(provider)

        # Set up rotator
        rotator = PasswordRotator(
            "password_gen",
            {
                "length": 16,
                "use_symbols": True,
                "use_numbers": True,
                "use_uppercase": True,
                "use_lowercase": True,
            },
        )
        self.engine.register_rotator(rotator)

        # Store initial encrypted secrets
        provider.update_secret("db_password", "initial_db_password")
        provider.update_secret("api_key", "initial_api_key")

    def tearDown(self):
        """Clean up"""
        import shutil

        os.unlink(self.temp_file.name)
        if Path(self.temp_backup_dir).exists():
            shutil.rmtree(self.temp_backup_dir)

        if os.path.exists(self.temp_key_file.name):
            os.unlink(self.temp_key_file.name)

    @patch("secret_rotator.rotation_engine.settings")
    def test_complete_encrypted_rotation_workflow(self, mock_settings):
        """Test complete rotation workflow with encryption and backup"""
        mock_settings.get.return_value = True

        # Add rotation job
        job = {
            "name": "database_password",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "db_password",
        }
        self.engine.add_rotation_job(job)

        # Get initial password
        provider = self.engine.providers["file_storage"]
        initial_password = provider.get_secret("db_password")
        self.assertEqual(initial_password, "initial_db_password")

        # Perform rotation
        results = self.engine.rotate_all_secrets()

        # Verify rotation succeeded
        self.assertTrue(results["database_password"])

        # Verify password changed
        new_password = provider.get_secret("db_password")
        self.assertNotEqual(initial_password, new_password)
        self.assertEqual(len(new_password), 16)

        # Verify encrypted backup was created
        backups = self.engine.backup_manager.list_backups(
            secret_id="db_password", mask_values=False
        )
        self.assertEqual(len(backups), 1)
        self.assertTrue(backups[0]["encrypted"])

        # Verify stored secret is encrypted in file
        import json

        with open(self.temp_file.name, "r") as f:
            file_contents = json.load(f)
            stored_value = file_contents["db_password"]
            # Should be encrypted (not equal to plaintext)
            self.assertNotEqual(stored_value, new_password)

    @patch("secret_rotator.rotation_engine.settings")
    def test_encrypted_rotation_and_restore_workflow(self, mock_settings):
        """Test rotation followed by restore from encrypted backup"""
        mock_settings.get.return_value = True

        # Add rotation job
        job = {
            "name": "database_password",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "db_password",
        }
        self.engine.add_rotation_job(job)

        provider = self.engine.providers["file_storage"]
        initial_password = provider.get_secret("db_password")

        # Perform rotation
        self.engine.rotate_all_secrets()
        new_password = provider.get_secret("db_password")
        self.assertNotEqual(initial_password, new_password)

        # Get encrypted backup
        backups = self.engine.backup_manager.list_backups(
            secret_id="db_password", mask_values=False
        )
        backup_file = backups[0]["backup_file"]

        # Restore from encrypted backup (with automatic decryption)
        backup_data = self.engine.backup_manager.restore_backup(backup_file, decrypt=True)
        provider.update_secret("db_password", backup_data["old_value"])

        # Verify password was restored to original
        restored_password = provider.get_secret("db_password")
        self.assertEqual(restored_password, initial_password)

        # Verify it's still encrypted in storage
        import json

        with open(self.temp_file.name, "r") as f:
            file_contents = json.load(f)
            stored_value = file_contents["db_password"]
            self.assertNotEqual(stored_value, restored_password)

    @patch("secret_rotator.rotation_engine.settings")
    def test_multiple_encrypted_secrets_rotation(self, mock_settings):
        """Test rotating multiple encrypted secrets"""
        mock_settings.get.return_value = True

        # Add multiple jobs
        jobs = [
            {
                "name": "database_password",
                "provider": "file_storage",
                "rotator": "password_gen",
                "secret_id": "db_password",
            },
            {
                "name": "api_key",
                "provider": "file_storage",
                "rotator": "password_gen",
                "secret_id": "api_key",
            },
        ]

        for job in jobs:
            self.engine.add_rotation_job(job)

        provider = self.engine.providers["file_storage"]
        initial_db_password = provider.get_secret("db_password")
        initial_api_key = provider.get_secret("api_key")

        # Perform rotation
        results = self.engine.rotate_all_secrets()

        # Verify all rotations succeeded
        self.assertTrue(results["database_password"])
        self.assertTrue(results["api_key"])

        # Verify both secrets changed
        new_db_password = provider.get_secret("db_password")
        new_api_key = provider.get_secret("api_key")

        self.assertNotEqual(initial_db_password, new_db_password)
        self.assertNotEqual(initial_api_key, new_api_key)

        # Verify encrypted backups for both secrets
        db_backups = self.engine.backup_manager.list_backups(
            secret_id="db_password", mask_values=False
        )
        api_backups = self.engine.backup_manager.list_backups(
            secret_id="api_key", mask_values=False
        )

        self.assertEqual(len(db_backups), 1)
        self.assertEqual(len(api_backups), 1)
        self.assertTrue(db_backups[0]["encrypted"])
        self.assertTrue(api_backups[0]["encrypted"])

    @patch("secret_rotator.rotation_engine.settings")
    def test_rotation_failure_handling_with_encryption(self, mock_settings):
        """Test that one failure doesn't stop other rotations"""
        mock_settings.get.return_value = True

        # Add valid and invalid jobs
        jobs = [
            {
                "name": "valid_job",
                "provider": "file_storage",
                "rotator": "password_gen",
                "secret_id": "db_password",
            },
            {
                "name": "invalid_job",
                "provider": "nonexistent_provider",
                "rotator": "password_gen",
                "secret_id": "api_key",
            },
        ]

        for job in jobs:
            self.engine.add_rotation_job(job)

        # Perform rotation
        results = self.engine.rotate_all_secrets()

        # Verify valid job succeeded and invalid job failed
        self.assertTrue(results["valid_job"])
        self.assertFalse(results["invalid_job"])

        # Verify backup was created only for successful rotation
        db_backups = self.engine.backup_manager.list_backups(secret_id="db_password")
        api_backups = self.engine.backup_manager.list_backups(secret_id="api_key")

        self.assertEqual(len(db_backups), 1)
        self.assertEqual(len(api_backups), 0)

    @patch("secret_rotator.rotation_engine.settings")
    def test_sequential_encrypted_rotations_create_multiple_backups(self, mock_settings):
        """Test that multiple rotations create separate encrypted backups"""
        mock_settings.get.return_value = True

        job = {
            "name": "database_password",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "db_password",
        }
        self.engine.add_rotation_job(job)

        provider = self.engine.providers["file_storage"]

        # Perform first rotation
        self.engine.rotate_all_secrets()
        first_password = provider.get_secret("db_password")

        # Perform second rotation
        self.engine.rotate_all_secrets()
        second_password = provider.get_secret("db_password")

        # Passwords should be different
        self.assertNotEqual(first_password, second_password)

        # Verify two encrypted backups were created
        backups = self.engine.backup_manager.list_backups(
            secret_id="db_password", mask_values=False
        )
        self.assertEqual(len(backups), 2)

        # Both should be encrypted
        for backup in backups:
            self.assertTrue(backup["encrypted"])

        # Verify backups have different values
        backup_data_0 = self.engine.backup_manager.restore_backup(
            backups[0]["backup_file"], decrypt=True
        )
        backup_data_1 = self.engine.backup_manager.restore_backup(
            backups[1]["backup_file"], decrypt=True
        )

        # New values should be different
        self.assertNotEqual(backup_data_0["new_value"], backup_data_1["new_value"])

    @patch("secret_rotator.rotation_engine.settings")
    def test_end_to_end_with_encryption_validation(self, mock_settings):
        """Test complete end-to-end workflow with encryption validation"""
        mock_settings.get.return_value = True

        job = {
            "name": "database_password",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "db_password",
        }
        self.engine.add_rotation_job(job)

        provider = self.engine.providers["file_storage"]
        rotator = self.engine.rotators["password_gen"]

        # Verify provider encryption is working
        self.assertTrue(provider.validate_connection())

        # Perform rotation
        results = self.engine.rotate_all_secrets()
        self.assertTrue(results["database_password"])

        # Get new password and validate it
        new_password = provider.get_secret("db_password")
        self.assertTrue(rotator.validate_secret(new_password))

        # Verify encrypted backup exists and is valid
        backups = self.engine.backup_manager.list_backups(secret_id="db_password")
        self.assertEqual(len(backups), 1)

        backup_file = backups[0]["backup_file"]

        # Verify backup integrity (includes decryption test)
        is_valid = self.engine.backup_manager.verify_backup_integrity(backup_file)
        self.assertTrue(is_valid)

        # Restore and verify decryption works
        backup_data = self.engine.backup_manager.restore_backup(backup_file, decrypt=True)
        self.assertIn("secret_id", backup_data)
        self.assertIn("old_value", backup_data)
        self.assertIn("new_value", backup_data)
        self.assertEqual(backup_data["new_value"], new_password)

        # Verify the old value is the initial value we set
        self.assertEqual(backup_data["old_value"], "initial_db_password")

    @patch("secret_rotator.rotation_engine.settings")
    def test_encryption_consistency_across_components(self, mock_settings):
        """Test that encryption is consistent across all components"""
        mock_settings.get.return_value = True

        job = {
            "name": "test_job",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "db_password",
        }
        self.engine.add_rotation_job(job)

        provider = self.engine.providers["file_storage"]

        # Perform rotation
        self.engine.rotate_all_secrets()

        # Get the new password through provider
        new_password = provider.get_secret("db_password")

        # Get the backup and decrypt it
        backups = self.engine.backup_manager.list_backups(
            secret_id="db_password", mask_values=False
        )
        backup_data = self.engine.backup_manager.restore_backup(
            backups[0]["backup_file"], decrypt=True
        )

        # The new value in backup should match what provider returns
        self.assertEqual(backup_data["new_value"], new_password)

        # Verify storage file is encrypted
        import json

        with open(self.temp_file.name, "r") as f:
            file_contents = json.load(f)
            stored_encrypted = file_contents["db_password"]

            # Should be encrypted (different from plaintext)
            self.assertNotEqual(stored_encrypted, new_password)

            # Should be base64-like
            self.assertTrue(len(stored_encrypted) > len(new_password))


class TestIntegrationPlaintextMode(unittest.TestCase):
    """Integration tests without encryption for backward compatibility"""

    def setUp(self):
        """Set up test fixtures without encryption"""
        self.temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.temp_file.write('{"db_password": "initial_password"}')
        self.temp_file.close()

        self.temp_backup_dir = tempfile.mkdtemp()

        self.engine = RotationEngine()
        self.engine.backup_manager = BackupManager(
            backup_dir=self.temp_backup_dir, encrypt_backups=False
        )

        # Set up provider without encryption
        provider = FileSecretProvider(
            "file_storage", {"file_path": self.temp_file.name, "encrypt_secrets": False}
        )
        self.engine.register_provider(provider)

        # Set up rotator
        rotator = PasswordRotator(
            "password_gen",
            {
                "length": 16,
                "use_symbols": True,
                "use_numbers": True,
                "use_uppercase": True,
                "use_lowercase": True,
            },
        )
        self.engine.register_rotator(rotator)

    def tearDown(self):
        """Clean up"""
        import os
        import shutil

        os.unlink(self.temp_file.name)
        if Path(self.temp_backup_dir).exists():
            shutil.rmtree(self.temp_backup_dir)

    @patch("secret_rotator.rotation_engine.settings")
    def test_plaintext_rotation_workflow(self, mock_settings):
        """Test rotation works in plaintext mode (backward compatibility)"""
        mock_settings.get.return_value = True

        job = {
            "name": "database_password",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "db_password",
        }
        self.engine.add_rotation_job(job)

        # Perform rotation
        results = self.engine.rotate_all_secrets()
        self.assertTrue(results["database_password"])

        # Verify plaintext backup
        backups = self.engine.backup_manager.list_backups(
            secret_id="db_password", mask_values=False
        )
        self.assertEqual(len(backups), 1)
        self.assertFalse(backups[0]["encrypted"])


if __name__ == "__main__":
    unittest.main()

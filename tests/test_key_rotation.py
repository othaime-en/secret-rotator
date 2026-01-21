import unittest
import tempfile
import json
import os
import shutil
from pathlib import Path

from secret_rotator.encryption_manager import EncryptionManager
from secret_rotator.providers.file_provider import FileSecretProvider


class TestMasterKeyRotation(unittest.TestCase):
    """Integration tests for master key rotation and two-phase commit"""

    def setUp(self):
        """Set up test environment before each test"""
        # Create temporary directory for test files
        self.test_dir = tempfile.mkdtemp()
        self.key_file = Path(self.test_dir) / ".master.key"
        self.secrets_file = Path(self.test_dir) / "secrets.json"
        
        # Helper to cleanup in tearDown
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        """Cleanup temporary directory"""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_rotation_preserves_secrets(self):
        """Test that rotation preserves all secret values"""
        # Step 1: Create encryption manager and test secrets
        em = EncryptionManager(key_file=str(self.key_file))

        test_secrets = {
            "secret1": "password123",
            "secret2": "api_key_xyz_789",
            "secret3": "database_connection_string",
            "secret4": "jwt_signing_secret_abcdef",
        }

        # Encrypt and store secrets
        encrypted_secrets = {}
        for secret_id, value in test_secrets.items():
            encrypted_secrets[secret_id] = em.encrypt(value)

        with open(self.secrets_file, "w") as f:
            json.dump(encrypted_secrets, f, indent=2)

        # Step 2: Create a provider
        provider = FileSecretProvider(
            name="test_provider",
            config={
                "file_path": str(self.secrets_file),
                "encrypt_secrets": True,
                "encryption_key_file": str(self.key_file),
            },
        )

        # Step 3: Verify secrets can be read before rotation
        for secret_id, expected_value in test_secrets.items():
            actual_value = provider.get_secret(secret_id)
            self.assertEqual(actual_value, expected_value)

        # Step 4: Perform key rotation
        providers = {"test_provider": provider}
        success = em.rotate_master_key(providers=providers)

        self.assertTrue(success, "Key rotation failed")

        # Step 5: Verify secrets can still be read after rotation
        for secret_id, expected_value in test_secrets.items():
            actual_value = provider.get_secret(secret_id)
            self.assertEqual(actual_value, expected_value)

        # Step 6: Verify key ID changed
        with open(self.key_file, "r") as f:
            key_data = json.load(f)

        self.assertIn("metadata", key_data)
        self.assertIn("key_id", key_data["metadata"])
        self.assertIn("rotated_from", key_data["metadata"])

    def test_rotation_rollback_on_failure(self):
        """Test that rotation rolls back on failure"""
        # Step 1: Create encryption manager and secrets
        em = EncryptionManager(key_file=str(self.key_file))

        original_key_id = em.key_metadata.get("key_id")

        test_secrets = {"secret1": "password123", "secret2": "api_key_xyz"}

        encrypted_secrets = {}
        for secret_id, value in test_secrets.items():
            encrypted_secrets[secret_id] = em.encrypt(value)

        with open(self.secrets_file, "w") as f:
            json.dump(encrypted_secrets, f, indent=2)

        # Step 2: Create provider
        provider = FileSecretProvider(
            name="test_provider",
            config={
                "file_path": str(self.secrets_file),
                "encrypt_secrets": True,
                "encryption_key_file": str(self.key_file),
            },
        )

        # Step 3: Corrupt the secrets file to force failure
        with open(self.secrets_file, "w") as f:
            f.write("{ invalid json to trigger failure")

        # Step 4: Attempt rotation (should fail)
        providers = {"test_provider": provider}
        success = em.rotate_master_key(providers=providers)

        self.assertFalse(success, "Rotation should have failed")

        # Step 5: Verify key was NOT changed (rollback worked)
        current_key_id = em.key_metadata.get("key_id")
        self.assertEqual(
            current_key_id,
            original_key_id,
            "Key ID changed despite failure (rollback failed)",
        )

        # Step 6: Verify original key file still exists
        self.assertTrue(self.key_file.exists(), "Master key file was deleted")

    def test_rotation_with_empty_secrets(self):
        """Test rotation with no secrets (edge case)"""
        # Create encryption manager
        em = EncryptionManager(key_file=str(self.key_file))
        original_key_id = em.key_metadata.get("key_id")

        # Create empty secrets file
        with open(self.secrets_file, "w") as f:
            json.dump({}, f)

        provider = FileSecretProvider(
            name="test_provider",
            config={
                "file_path": str(self.secrets_file),
                "encrypt_secrets": True,
                "encryption_key_file": str(self.key_file),
            },
        )

        # Rotate
        providers = {"test_provider": provider}
        success = em.rotate_master_key(providers=providers)

        self.assertTrue(success, "Rotation should succeed even with empty secrets")

        # Verify key changed
        new_key_id = em.key_metadata.get("key_id")
        self.assertNotEqual(new_key_id, original_key_id, "Key ID should have changed")


if __name__ == "__main__":
    unittest.main()
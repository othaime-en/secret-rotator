import unittest
import tempfile
import json
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "src"))

from providers.file_provider import FileSecretProvider
from encryption_manager import EncryptionManager

class TestFileProviderWithEncryption(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        self.temp_file.write('{}')
        self.temp_file.close()
        
        self.config = {
            "file_path": self.temp_file.name,
            "encrypt_secrets": True,
            "encryption_key_file": 'config/.master.key'
        }
        self.provider = FileSecretProvider("test_provider", self.config)
    
    def tearDown(self):
        """Clean up test fixtures"""
        os.unlink(self.temp_file.name)
       
    def test_update_and_get_secret_encrypted(self):
        """Test storing and retrieving encrypted secret"""
        secret_id = "test_secret"
        secret_value = "my_secret_password"
        
        # Store the secret (should be encrypted)
        success = self.provider.update_secret(secret_id, secret_value)
        self.assertTrue(success)
        
        # Retrieve the secret (should be decrypted)
        retrieved_value = self.provider.get_secret(secret_id)
        self.assertEqual(retrieved_value, secret_value)
        
        # Verify it's actually encrypted in the file
        with open(self.temp_file.name, 'r') as f:
            file_contents = json.load(f)
            stored_value = file_contents[secret_id]
            # Encrypted value should not match plaintext
            self.assertNotEqual(stored_value, secret_value)
            # Should be base64 encoded (contains only alphanumeric + =)
            self.assertTrue(all(c.isalnum() or c in '=+/' for c in stored_value))
    
    def test_get_nonexistent_secret(self):
        """Test retrieving non-existent secret"""
        value = self.provider.get_secret("nonexistent")
        self.assertEqual(value, "")
    
    def test_multiple_secrets_encrypted(self):
        """Test storing multiple encrypted secrets"""
        secrets = {
            "db_password": "database_pass_123",
            "api_key": "api_key_abc_xyz",
            "token": "secure_token_789"
        }
        
        # Store all secrets
        for secret_id, secret_value in secrets.items():
            success = self.provider.update_secret(secret_id, secret_value)
            self.assertTrue(success)
        
        # Retrieve and verify all secrets
        for secret_id, expected_value in secrets.items():
            retrieved_value = self.provider.get_secret(secret_id)
            self.assertEqual(retrieved_value, expected_value)
    
    def test_validate_connection_with_encryption(self):
        """Test connection validation includes encryption check"""
        self.assertTrue(self.provider.validate_connection())
    
    def test_encryption_disabled(self):
        """Test provider works without encryption"""
        # Create provider without encryption
        config = {
            "file_path": self.temp_file.name,
            "encrypt_secrets": False
        }
        provider = FileSecretProvider("plain_provider", config)
        
        # Store and retrieve
        provider.update_secret("test", "plaintext_value")
        retrieved = provider.get_secret("test")
        self.assertEqual(retrieved, "plaintext_value")
        
        # Verify it's stored as plaintext
        with open(self.temp_file.name, 'r') as f:
            file_contents = json.load(f)
            self.assertEqual(file_contents["test"], "plaintext_value")
    
    def test_migrate_to_encrypted(self):
        """Test migrating plaintext secrets to encrypted"""
        # Start with plaintext secrets
        plaintext_secrets = {
            "secret1": "value1",
            "secret2": "value2",
            "secret3": "value3"
        }
        
        with open(self.temp_file.name, 'w') as f:
            json.dump(plaintext_secrets, f)
        
        # Create provider with encryption enabled
        provider = FileSecretProvider("test_provider", self.config)
        
        # Run migration
        success = provider.migrate_to_encrypted()
        self.assertTrue(success)
        
        # Verify secrets are now encrypted
        with open(self.temp_file.name, 'r') as f:
            file_contents = json.load(f)
            for secret_id in plaintext_secrets:
                stored_value = file_contents[secret_id]
                # Should be encrypted (not matching plaintext)
                self.assertNotEqual(stored_value, plaintext_secrets[secret_id])
        
        # Verify secrets can still be retrieved correctly
        for secret_id, expected_value in plaintext_secrets.items():
            retrieved = provider.get_secret(secret_id)
            self.assertEqual(retrieved, expected_value)
    
    def test_migration_idempotent(self):
        """Test that running migration twice doesn't break anything"""
        # Setup with plaintext
        with open(self.temp_file.name, 'w') as f:
            json.dump({"test": "value"}, f)
        
        provider = FileSecretProvider("test_provider", self.config)
        
        # Run migration twice
        provider.migrate_to_encrypted()
        provider.migrate_to_encrypted()
        
        # Should still work
        retrieved = provider.get_secret("test")
        self.assertEqual(retrieved, "value")
    
    def test_update_overwrites_encrypted_secret(self):
        """Test updating an already encrypted secret"""
        secret_id = "test_secret"
        
        # Store first value
        self.provider.update_secret(secret_id, "first_value")
        self.assertEqual(self.provider.get_secret(secret_id), "first_value")
        
        # Update with second value
        self.provider.update_secret(secret_id, "second_value")
        self.assertEqual(self.provider.get_secret(secret_id), "second_value")
        
        # Verify only one entry in file
        with open(self.temp_file.name, 'r') as f:
            file_contents = json.load(f)
            self.assertEqual(len(file_contents), 1)
            self.assertIn(secret_id, file_contents)
    
    def test_empty_secret_value(self):
        """Test handling of empty secret values"""
        success = self.provider.update_secret("empty_secret", "")
        self.assertTrue(success)
        
        retrieved = self.provider.get_secret("empty_secret")
        self.assertEqual(retrieved, "")
    
    def test_special_characters_in_secret(self):
        """Test secrets with special characters are encrypted/decrypted correctly"""
        special_secrets = {
            "special1": "p@ssw0rd!#$%",
            "special2": "key_with_unicode_café",
            "special3": "multi\nline\nsecret",
            "special4": '{"json": "value"}',
        }
        
        for secret_id, secret_value in special_secrets.items():
            self.provider.update_secret(secret_id, secret_value)
            retrieved = self.provider.get_secret(secret_id)
            self.assertEqual(retrieved, secret_value)

class TestFileProviderPlaintext(unittest.TestCase):
    """Tests for provider without encryption (backward compatibility)"""
    
    def setUp(self):
        """Set up test fixtures without encryption"""
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        self.temp_file.write('{"test_secret": "test_value"}')
        self.temp_file.close()
        
        self.config = {
            "file_path": self.temp_file.name,
            "encrypt_secrets": False
        }
        self.provider = FileSecretProvider("test_provider", self.config)
    
    def tearDown(self):
        """Clean up test fixtures"""
        os.unlink(self.temp_file.name)
    
    def test_get_secret_plaintext(self):
        """Test retrieving secret without encryption"""
        value = self.provider.get_secret("test_secret")
        self.assertEqual(value, "test_value")
    
    def test_update_secret_plaintext(self):
        """Test updating secret without encryption"""
        success = self.provider.update_secret("test_secret", "new_value")
        self.assertTrue(success)
        
        # Verify the update
        value = self.provider.get_secret("test_secret")
        self.assertEqual(value, "new_value")
        
        # Verify it's stored as plaintext
        with open(self.temp_file.name, 'r') as f:
            file_contents = json.load(f)
            self.assertEqual(file_contents["test_secret"], "new_value")
    
    def test_validate_connection_plaintext(self):
        """Test connection validation without encryption"""
        self.assertTrue(self.provider.validate_connection())



if __name__ == '__main__':
    unittest.main()
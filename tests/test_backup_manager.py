import unittest
import tempfile
import json
import sys
import time
import os
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "src"))

from backup_manager import BackupManager


class TestBackupManagerWithEncryption(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures with encryption enabled"""
        self.temp_backup_dir = tempfile.mkdtemp()
        self.backup_manager = BackupManager(
            backup_dir=self.temp_backup_dir,
            encrypt_backups=True
        )
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        if Path(self.temp_backup_dir).exists():
            shutil.rmtree(self.temp_backup_dir)
    
    def test_create_encrypted_backup(self):
        """Test creating an encrypted backup"""
        secret_id = "test_secret"
        old_value = "old_password"
        new_value = "new_password"
        
        backup_path = self.backup_manager.create_backup(secret_id, old_value, new_value)
        
        self.assertTrue(Path(backup_path).exists())
        
        # Verify backup content
        with open(backup_path, 'r') as f:
            backup_data = json.load(f)
        
        self.assertEqual(backup_data['secret_id'], secret_id)
        self.assertTrue(backup_data['encrypted'])
        
        # Verify values are encrypted (not plaintext)
        self.assertNotEqual(backup_data['old_value'], old_value)
        self.assertNotEqual(backup_data['new_value'], new_value)
        
        # Encrypted values should be base64-like strings
        self.assertIsInstance(backup_data['old_value'], str)
        self.assertIsInstance(backup_data['new_value'], str)
    
    def test_restore_encrypted_backup(self):
        """Test restoring from encrypted backup"""
        secret_id = "test_secret"
        old_value = "old_password"
        new_value = "new_password"
        
        # Create backup
        backup_path = self.backup_manager.create_backup(secret_id, old_value, new_value)
        
        # Restore the backup (with decryption)
        restored_data = self.backup_manager.restore_backup(backup_path, decrypt=True)
        
        self.assertEqual(restored_data['secret_id'], secret_id)
        self.assertEqual(restored_data['old_value'], old_value)
        self.assertEqual(restored_data['new_value'], new_value)
    
    def test_restore_backup_without_decryption(self):
        """Test restoring backup without decrypting"""
        secret_id = "test_secret"
        old_value = "old_password"
        new_value = "new_password"
        
        backup_path = self.backup_manager.create_backup(secret_id, old_value, new_value)
        
        # Restore without decryption
        restored_data = self.backup_manager.restore_backup(backup_path, decrypt=False)
        
        # Values should still be encrypted
        self.assertNotEqual(restored_data['old_value'], old_value)
        self.assertNotEqual(restored_data['new_value'], new_value)
    
    def test_restore_nonexistent_backup(self):
        """Test restoring non-existent backup raises error"""
        with self.assertRaises(FileNotFoundError):
            self.backup_manager.restore_backup("nonexistent_backup.json")
    
    def test_list_backups_with_masking(self):
        """Test listing backups with masked values"""
        # Create multiple backups
        self.backup_manager.create_backup("secret1", "password123", "newpass456")
        self.backup_manager.create_backup("secret2", "apikey789", "newapikey012")
        
        # List with masking (default)
        backups = self.backup_manager.list_backups(mask_values=True)
        
        self.assertEqual(len(backups), 2)
        
        # Verify values are masked
        for backup in backups:
            self.assertIn('old_value_masked', backup)
            self.assertIn('new_value_masked', backup)
            # Original values should be removed for security
            self.assertNotIn('old_value', backup)
            self.assertNotIn('new_value', backup)
            # Masked values should be short
            self.assertLessEqual(len(backup['old_value_masked']), 10)
    
    def test_list_backups_without_masking(self):
        """Test listing backups without masking (for internal use)"""
        self.backup_manager.create_backup("secret1", "password123", "newpass456")
        
        # List without masking
        backups = self.backup_manager.list_backups(mask_values=False)
        
        self.assertEqual(len(backups), 1)
        
        # Original (encrypted) values should be present
        backup = backups[0]
        self.assertIn('old_value', backup)
        self.assertIn('new_value', backup)
    
    def test_list_backups_for_specific_secret(self):
        """Test listing backups for specific secret"""
        # Create backups for different secrets
        self.backup_manager.create_backup("secret1", "old1", "new1")
        self.backup_manager.create_backup("secret2", "old2", "new2")
        self.backup_manager.create_backup("secret1", "old3", "new3")
        
        # List backups for specific secret
        secret1_backups = self.backup_manager.list_backups(secret_id="secret1")
        self.assertEqual(len(secret1_backups), 2)
        
        # Verify all are for secret1
        for backup in secret1_backups:
            self.assertEqual(backup['secret_id'], "secret1")
    
 

if __name__ == '__main__':
    unittest.main()

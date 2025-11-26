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
    
    def test_list_backups_sorting(self):
        """Test backups are sorted by timestamp (newest first)"""
        # Create backups with slight delays
        self.backup_manager.create_backup("test", "old1", "new1")
        time.sleep(0.01)
        self.backup_manager.create_backup("test", "old2", "new2")
        time.sleep(0.01)
        self.backup_manager.create_backup("test", "old3", "new3")
        
        backups = self.backup_manager.list_backups(secret_id="test", mask_values=False)
        
        # Verify newest is first
        self.assertEqual(len(backups), 3)
        # Timestamps should be in descending order
        timestamps = [b['timestamp'] for b in backups]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))
    
    def test_cleanup_old_backups(self):
        """Test cleaning up old backups"""
        # Create a backup
        backup_path = self.backup_manager.create_backup("test", "old", "new")
        
        # Modify file timestamp to be 31 days old
        old_time = time.time() - (31 * 24 * 60 * 60)
        os.utime(backup_path, (old_time, old_time))
        
        # Cleanup backups older than 30 days
        removed = self.backup_manager.cleanup_old_backups(days_to_keep=30)
        
        self.assertEqual(removed, 1)
        self.assertFalse(Path(backup_path).exists())
    
    def test_cleanup_keeps_recent_backups(self):
        """Test cleanup keeps recent backups"""
        # Create a recent backup
        backup_path = self.backup_manager.create_backup("test", "old", "new")
        
        # Cleanup old backups
        removed = self.backup_manager.cleanup_old_backups(days_to_keep=30)
        
        # Should not remove recent backup
        self.assertEqual(removed, 0)
        self.assertTrue(Path(backup_path).exists())
    
    def test_verify_backup_integrity(self):
        """Test backup integrity verification"""
        secret_id = "test_secret"
        old_value = "old_password"
        new_value = "new_password"
        
        backup_path = self.backup_manager.create_backup(secret_id, old_value, new_value)
        
        # Verify integrity
        is_valid = self.backup_manager.verify_backup_integrity(backup_path)
        self.assertTrue(is_valid)
    
    def test_verify_corrupted_backup(self):
        """Test integrity check fails for corrupted backup"""
        # Create a backup
        backup_path = self.backup_manager.create_backup("test", "old", "new")
        
        # Corrupt the backup file
        with open(backup_path, 'w') as f:
            f.write('corrupted data')
        
        # Verify should fail
        is_valid = self.backup_manager.verify_backup_integrity(backup_path)
        self.assertFalse(is_valid)
    
    def test_export_backup_metadata(self):
        """Test exporting backup metadata"""
        # Create some backups
        self.backup_manager.create_backup("secret1", "old1", "new1")
        self.backup_manager.create_backup("secret2", "old2", "new2")
        
        metadata = self.backup_manager.export_backup_metadata()
        
        self.assertEqual(metadata['total_backups'], 2)
        self.assertEqual(metadata['secrets_with_backups'], 2)
        self.assertTrue(metadata['encryption_enabled'])
        self.assertIn('backup_directory', metadata)
        self.assertIsNotNone(metadata['oldest_backup'])
        self.assertIsNotNone(metadata['newest_backup'])


class TestBackupManagerWithoutEncryption(unittest.TestCase):
    """Test backup manager with encryption disabled"""
    
    def setUp(self):
        """Set up test fixtures without encryption"""
        self.temp_backup_dir = tempfile.mkdtemp()
        self.backup_manager = BackupManager(
            backup_dir=self.temp_backup_dir,
            encrypt_backups=False
        )
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        if Path(self.temp_backup_dir).exists():
            shutil.rmtree(self.temp_backup_dir)
    
    def test_create_plaintext_backup(self):
        """Test creating plaintext backup"""
        secret_id = "test_secret"
        old_value = "old_password"
        new_value = "new_password"
        
        backup_path = self.backup_manager.create_backup(secret_id, old_value, new_value)
        
        # Verify backup content
        with open(backup_path, 'r') as f:
            backup_data = json.load(f)
        
        self.assertEqual(backup_data['secret_id'], secret_id)
        self.assertFalse(backup_data['encrypted'])
        
        # Values should be plaintext
        self.assertEqual(backup_data['old_value'], old_value)
        self.assertEqual(backup_data['new_value'], new_value)
    
    def test_restore_plaintext_backup(self):
        """Test restoring plaintext backup"""
        secret_id = "test_secret"
        old_value = "old_password"
        new_value = "new_password"
        
        backup_path = self.backup_manager.create_backup(secret_id, old_value, new_value)
        
        # Restore
        restored_data = self.backup_manager.restore_backup(backup_path)
        
        self.assertEqual(restored_data['old_value'], old_value)
        self.assertEqual(restored_data['new_value'], new_value)
    
    def test_export_metadata_without_encryption(self):
        """Test metadata export shows encryption disabled"""
        self.backup_manager.create_backup("test", "old", "new")
        
        metadata = self.backup_manager.export_backup_metadata()
        
        self.assertFalse(metadata['encryption_enabled'])
        self.assertEqual(metadata['total_backups'], 1)


class TestBackupManagerEdgeCases(unittest.TestCase):
    """Test edge cases and error scenarios"""
    
    def setUp(self):
        """Set up test fixtures"""
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
    
    def test_backup_with_empty_values(self):
        """Test creating backup with empty string values"""
        backup_path = self.backup_manager.create_backup("test", "", "")
        
        restored = self.backup_manager.restore_backup(backup_path)
        self.assertEqual(restored['old_value'], "")
        self.assertEqual(restored['new_value'], "")
    
    def test_backup_with_special_characters(self):
        """Test backup with special characters in values"""
        special_values = {
            "old": "p@ssw0rd!#$%\n\t",
            "new": "unicode_café_日本語"
        }
        
        backup_path = self.backup_manager.create_backup(
            "test",
            special_values["old"],
            special_values["new"]
        )
        
        restored = self.backup_manager.restore_backup(backup_path)
        self.assertEqual(restored['old_value'], special_values["old"])
        self.assertEqual(restored['new_value'], special_values["new"])
    
    def test_backup_with_very_long_values(self):
        """Test backup with very long secret values"""
        long_value = "x" * 10000
        
        backup_path = self.backup_manager.create_backup("test", long_value, long_value)
        
        restored = self.backup_manager.restore_backup(backup_path)
        self.assertEqual(len(restored['old_value']), 10000)
    
    def test_list_backups_empty_directory(self):
        """Test listing backups when directory is empty"""
        backups = self.backup_manager.list_backups()
        self.assertEqual(len(backups), 0)
    
    def test_verify_nonexistent_backup(self):
        """Test verifying nonexistent backup"""
        is_valid = self.backup_manager.verify_backup_integrity("nonexistent.json")
        self.assertFalse(is_valid)
    
    def test_multiple_rapid_backups_same_secret(self):
        """Test creating multiple backups of same secret rapidly"""
        secret_id = "test_secret"
        
        backup_paths = []
        for i in range(5):
            backup_path = self.backup_manager.create_backup(
                secret_id,
                f"old_value_{i}",
                f"new_value_{i}"
            )
            backup_paths.append(backup_path)
            time.sleep(0.001)  # Tiny delay to ensure different timestamps
        
        # All backups should exist
        for backup_path in backup_paths:
            self.assertTrue(Path(backup_path).exists())
        
        # Should have 5 backups
        backups = self.backup_manager.list_backups(secret_id=secret_id)
        self.assertEqual(len(backups), 5)



if __name__ == '__main__':
    unittest.main()

import unittest
import tempfile
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "src"))

from backup_manager import BackupManager


class TestBackupManager(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_backup_dir = tempfile.mkdtemp()
        self.backup_manager = BackupManager(backup_dir=self.temp_backup_dir)
    
    def tearDown(self):
        """Clean up test fixtures"""
        import shutil
        if Path(self.temp_backup_dir).exists():
            shutil.rmtree(self.temp_backup_dir)
    
    def test_create_backup(self):
        """Test creating a backup"""
        secret_id = "test_secret"
        old_value = "old_password"
        new_value = "new_password"
        
        backup_path = self.backup_manager.create_backup(secret_id, old_value, new_value)
        
        self.assertTrue(Path(backup_path).exists())
        
        # Verify backup content
        with open(backup_path, 'r') as f:
            backup_data = json.load(f)
        
        self.assertEqual(backup_data['secret_id'], secret_id)
        self.assertEqual(backup_data['old_value'], old_value)
        self.assertEqual(backup_data['new_value'], new_value)
    
    def test_restore_backup(self):
        """Test restoring from backup"""
        # Create a backup first
        secret_id = "test_secret"
        old_value = "old_password"
        new_value = "new_password"
        
        backup_path = self.backup_manager.create_backup(secret_id, old_value, new_value)
        
        # Restore the backup
        restored_data = self.backup_manager.restore_backup(backup_path)
        
        self.assertEqual(restored_data['secret_id'], secret_id)
        self.assertEqual(restored_data['old_value'], old_value)
        self.assertEqual(restored_data['new_value'], new_value)
    
    def test_restore_nonexistent_backup(self):
        """Test restoring non-existent backup raises error"""
        with self.assertRaises(FileNotFoundError):
            self.backup_manager.restore_backup("nonexistent_backup.json")
    
    def test_list_backups(self):
        """Test listing all backups"""
        # Create multiple backups
        self.backup_manager.create_backup("secret1", "old1", "new1")
        self.backup_manager.create_backup("secret2", "old2", "new2")
        self.backup_manager.create_backup("secret1", "old3", "new3")
        
        # List all backups
        all_backups = self.backup_manager.list_backups()
        self.assertEqual(len(all_backups), 3)
        
        # List backups for specific secret
        secret1_backups = self.backup_manager.list_backups(secret_id="secret1")
        self.assertEqual(len(secret1_backups), 2)
    
    def test_list_backups_sorting(self):
        """Test backups are sorted by timestamp (newest first)"""
        import time
        
        # Create backups with slight delays to ensure different timestamps
        self.backup_manager.create_backup("test", "old1", "new1")
        time.sleep(0.01)
        self.backup_manager.create_backup("test", "old2", "new2")
        time.sleep(0.01)
        self.backup_manager.create_backup("test", "old3", "new3")
        
        backups = self.backup_manager.list_backups(secret_id="test")
        
        # Verify newest is first
        self.assertEqual(len(backups), 3)
        self.assertEqual(backups[0]['new_value'], "new3")
        self.assertEqual(backups[1]['new_value'], "new2")
        self.assertEqual(backups[2]['new_value'], "new1")
    
    def test_cleanup_old_backups(self):
        """Test cleaning up old backups"""
        import time
        import os
        
        # Create a backup
        backup_path = self.backup_manager.create_backup("test", "old", "new")
        
        # Modify file timestamp to be 31 days old
        old_time = time.time() - (31 * 24 * 60 * 60)
        Path(backup_path).touch()
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


if __name__ == '__main__':
    unittest.main()
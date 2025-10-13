import unittest
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).parent.parent / "src"))

from rotation_engine import RotationEngine
from backup_manager import BackupManager
from providers.file_provider import FileSecretProvider
from rotators.password_rotator import PasswordRotator


class TestIntegration(unittest.TestCase):
    """Integration tests for complete rotation workflow"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        self.temp_file.write('{"db_password": "initial_password", "api_key": "initial_key"}')
        self.temp_file.close()
        
        self.temp_backup_dir = tempfile.mkdtemp()
        
        self.engine = RotationEngine()
        self.engine.backup_manager = BackupManager(backup_dir=self.temp_backup_dir)
        
        # Set up provider
        provider = FileSecretProvider("file_storage", {
            "file_path": self.temp_file.name
        })
        self.engine.register_provider(provider)
        
        # Set up rotator
        rotator = PasswordRotator("password_gen", {
            "length": 16,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        })
        self.engine.register_rotator(rotator)
    
    def tearDown(self):
        """Clean up"""
        import os
        import shutil
        os.unlink(self.temp_file.name)
        if Path(self.temp_backup_dir).exists():
            shutil.rmtree(self.temp_backup_dir)
    
    @patch('rotation_engine.settings')
    def test_complete_rotation_workflow(self, mock_settings):
        """Test complete rotation workflow with backup"""
        mock_settings.get.return_value = True
        
        # Add rotation job
        job = {
            "name": "database_password",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "db_password"
        }
        self.engine.add_rotation_job(job)
        
        # Get initial password
        provider = self.engine.providers["file_storage"]
        initial_password = provider.get_secret("db_password")
        
        # Perform rotation
        results = self.engine.rotate_all_secrets()
        
        # Verify rotation succeeded
        self.assertTrue(results["database_password"])
        
        # Verify password changed
        new_password = provider.get_secret("db_password")
        self.assertNotEqual(initial_password, new_password)
        self.assertEqual(len(new_password), 16)
        
        # Verify backup was created
        backups = self.engine.backup_manager.list_backups(secret_id="db_password")
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]['old_value'], initial_password)
        self.assertEqual(backups[0]['new_value'], new_password)
    
    @patch('rotation_engine.settings')
    def test_rotation_and_restore_workflow(self, mock_settings):
        """Test rotation followed by restore from backup"""
        mock_settings.get.return_value = True
        
        # Add rotation job
        job = {
            "name": "database_password",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "db_password"
        }
        self.engine.add_rotation_job(job)
        
        provider = self.engine.providers["file_storage"]
        initial_password = provider.get_secret("db_password")
        
        # Perform rotation
        self.engine.rotate_all_secrets()
        new_password = provider.get_secret("db_password")
        
        # Get backup
        backups = self.engine.backup_manager.list_backups(secret_id="db_password")
        backup_file = backups[0]['backup_file']
        
        # Restore from backup
        backup_data = self.engine.backup_manager.restore_backup(backup_file)
        provider.update_secret("db_password", backup_data['old_value'])
        
        # Verify password was restored
        restored_password = provider.get_secret("db_password")
        self.assertEqual(restored_password, initial_password)
    
    @patch('rotation_engine.settings')
    def test_multiple_secrets_rotation(self, mock_settings):
        """Test rotating multiple different secrets"""
        mock_settings.get.return_value = True
        
        # Add multiple jobs
        jobs = [
            {
                "name": "database_password",
                "provider": "file_storage",
                "rotator": "password_gen",
                "secret_id": "db_password"
            },
            {
                "name": "api_key",
                "provider": "file_storage",
                "rotator": "password_gen",
                "secret_id": "api_key"
            }
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
        
        # Verify backups for both secrets
        db_backups = self.engine.backup_manager.list_backups(secret_id="db_password")
        api_backups = self.engine.backup_manager.list_backups(secret_id="api_key")
        
        self.assertEqual(len(db_backups), 1)
        self.assertEqual(len(api_backups), 1)
    
    @patch('rotation_engine.settings')
    def test_rotation_failure_handling(self, mock_settings):
        """Test that one failure doesn't stop other rotations"""
        mock_settings.get.return_value = True
        
        # Add valid and invalid jobs
        jobs = [
            {
                "name": "valid_job",
                "provider": "file_storage",
                "rotator": "password_gen",
                "secret_id": "db_password"
            },
            {
                "name": "invalid_job",
                "provider": "nonexistent_provider",
                "rotator": "password_gen",
                "secret_id": "api_key"
            }
        ]
        
        for job in jobs:
            self.engine.add_rotation_job(job)
        
        # Perform rotation
        results = self.engine.rotate_all_secrets()
        
        # Verify valid job succeeded and invalid job failed
        self.assertTrue(results["valid_job"])
        self.assertFalse(results["invalid_job"])
    
    @patch('rotation_engine.settings')
    def test_sequential_rotations_create_multiple_backups(self, mock_settings):
        """Test that multiple rotations create separate backups"""
        mock_settings.get.return_value = True
        
        job = {
            "name": "database_password",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "db_password"
        }
        self.engine.add_rotation_job(job)
        
        # Perform first rotation
        self.engine.rotate_all_secrets()
        
        # Perform second rotation
        self.engine.rotate_all_secrets()
        
        # Verify two backups were created
        backups = self.engine.backup_manager.list_backups(secret_id="db_password")
        self.assertEqual(len(backups), 2)
        
        # Verify backups have different values
        self.assertNotEqual(backups[0]['new_value'], backups[1]['new_value'])
    
    @patch('rotation_engine.settings')
    def test_end_to_end_with_validation(self, mock_settings):
        """Test complete end-to-end workflow with validation"""
        mock_settings.get.return_value = True
        
        job = {
            "name": "database_password",
            "provider": "file_storage",
            "rotator": "password_gen",
            "secret_id": "db_password"
        }
        self.engine.add_rotation_job(job)
        
        provider = self.engine.providers["file_storage"]
        rotator = self.engine.rotators["password_gen"]
        
        # Perform rotation
        results = self.engine.rotate_all_secrets()
        self.assertTrue(results["database_password"])
        
        # Get new password and validate it
        new_password = provider.get_secret("db_password")
        self.assertTrue(rotator.validate_secret(new_password))
        
        # Verify backup exists and is valid
        backups = self.engine.backup_manager.list_backups(secret_id="db_password")
        self.assertEqual(len(backups), 1)
        
        backup_data = self.engine.backup_manager.restore_backup(backups[0]['backup_file'])
        self.assertIn('secret_id', backup_data)
        self.assertIn('old_value', backup_data)
        self.assertIn('new_value', backup_data)
        self.assertEqual(backup_data['new_value'], new_password)


if __name__ == '__main__':
    unittest.main()
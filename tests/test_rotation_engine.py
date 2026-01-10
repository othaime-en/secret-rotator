import unittest
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).parent.parent / "src"))

from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.providers.file_provider import FileSecretProvider
from secret_rotator.rotators.password_rotator import PasswordRotator


class TestRotationEngine(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.engine = RotationEngine()
        
        # Create temporary file for testing
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        self.temp_file.write('{"test_secret": "old_value"}')
        self.temp_file.close()
        
        # Create temporary backup directory
        self.temp_backup_dir = tempfile.mkdtemp()
        
        # Register test provider
        self.provider = FileSecretProvider("test_provider", {
            "file_path": self.temp_file.name
        })
        self.engine.register_provider(self.provider)
        
        # Register test rotator
        self.rotator = PasswordRotator("test_rotator", {
            "length": 12,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        })
        self.engine.register_rotator(self.rotator)
    
    def tearDown(self):
        """Clean up test fixtures"""
        import os
        import shutil
        os.unlink(self.temp_file.name)
        if os.path.exists(self.temp_backup_dir):
            shutil.rmtree(self.temp_backup_dir)
    
    def test_register_provider(self):
        """Test provider registration"""
        self.assertIn("test_provider", self.engine.providers)
        self.assertEqual(self.engine.providers["test_provider"], self.provider)
    
    def test_register_rotator(self):
        """Test rotator registration"""
        self.assertIn("test_rotator", self.engine.rotators)
        self.assertEqual(self.engine.rotators["test_rotator"], self.rotator)
    
    def test_add_rotation_job_success(self):
        """Test adding valid rotation job"""
        job_config = {
            "name": "test_job",
            "provider": "test_provider",
            "rotator": "test_rotator",
            "secret_id": "test_secret"
        }
        result = self.engine.add_rotation_job(job_config)
        self.assertTrue(result)
        self.assertEqual(len(self.engine.rotation_jobs), 1)
    
    def test_add_rotation_job_missing_field(self):
        """Test adding job with missing required field"""
        job_config = {
            "name": "test_job",
            "provider": "test_provider"
            # Missing rotator and secret_id
        }
        result = self.engine.add_rotation_job(job_config)
        self.assertFalse(result)
        self.assertEqual(len(self.engine.rotation_jobs), 0)
    
    @patch('rotation_engine.settings')
    def test_rotate_secret_success(self, mock_settings):
        """Test successful secret rotation"""
        mock_settings.get.return_value = True
        
        job_config = {
            "name": "test_job",
            "provider": "test_provider",
            "rotator": "test_rotator",
            "secret_id": "test_secret"
        }
        
        result = self.engine.rotate_secret(job_config)
        self.assertTrue(result)
        
        # Verify secret was updated
        new_value = self.provider.get_secret("test_secret")
        self.assertNotEqual(new_value, "old_value")
        self.assertEqual(len(new_value), 12)
    
    def test_rotate_secret_invalid_provider(self):
        """Test rotation with non-existent provider"""
        job_config = {
            "name": "test_job",
            "provider": "nonexistent_provider",
            "rotator": "test_rotator",
            "secret_id": "test_secret"
        }
        
        result = self.engine.rotate_secret(job_config)
        self.assertFalse(result)
    
    def test_rotate_secret_invalid_rotator(self):
        """Test rotation with non-existent rotator"""
        job_config = {
            "name": "test_job",
            "provider": "test_provider",
            "rotator": "nonexistent_rotator",
            "secret_id": "test_secret"
        }
        
        result = self.engine.rotate_secret(job_config)
        self.assertFalse(result)
    
    @patch('rotation_engine.settings')
    def test_rotate_all_secrets(self, mock_settings):
        """Test rotating all configured secrets"""
        mock_settings.get.return_value = True
        
        # Add multiple jobs
        jobs = [
            {
                "name": "job1",
                "provider": "test_provider",
                "rotator": "test_rotator",
                "secret_id": "secret1"
            },
            {
                "name": "job2",
                "provider": "test_provider",
                "rotator": "test_rotator",
                "secret_id": "secret2"
            }
        ]
        
        for job in jobs:
            self.engine.add_rotation_job(job)
        
        results = self.engine.rotate_all_secrets()
        
        self.assertEqual(len(results), 2)
        self.assertIn("job1", results)
        self.assertIn("job2", results)


if __name__ == '__main__':
    unittest.main()
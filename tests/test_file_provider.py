import unittest
import tempfile
import json
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "src"))

from providers.file_provider import FileSecretProvider

class TestFileProvider(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        self.temp_file.write('{"test_secret": "test_value"}')
        self.temp_file.close()
        
        self.config = {"file_path": self.temp_file.name}
        self.provider = FileSecretProvider("test_provider", self.config)
    
    def tearDown(self):
        """Clean up test fixtures"""
        os.unlink(self.temp_file.name)
    
    def test_get_secret(self):
        """Test retrieving secret"""
        value = self.provider.get_secret("test_secret")
        self.assertEqual(value, "test_value")
    
    def test_get_nonexistent_secret(self):
        """Test retrieving non-existent secret"""
        value = self.provider.get_secret("nonexistent")
        self.assertEqual(value, "")
    
    def test_update_secret(self):
        """Test updating secret"""
        success = self.provider.update_secret("test_secret", "new_value")
        self.assertTrue(success)
        
        # Verify the update
        value = self.provider.get_secret("test_secret")
        self.assertEqual(value, "new_value")
    
    def test_validate_connection(self):
        """Test connection validation"""
        self.assertTrue(self.provider.validate_connection())

if __name__ == '__main__':
    unittest.main()
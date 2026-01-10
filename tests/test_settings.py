import unittest
import tempfile
import sys
from pathlib import Path
import yaml

from secret_rotator.config.settings import Settings

class TestSettings(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        # Create a temporary config file
        self.temp_config = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
        config_data = {
            'rotation': {
                'schedule': 'daily',
                'retry_attempts': 3,
                'timeout': 30
            },
            'logging': {
                'level': 'INFO',
                'file': 'logs/test.log'
            },
            'providers': {
                'file_storage': {
                    'type': 'file',
                    'file_path': 'data/secrets.json'
                }
            }
        }
        yaml.dump(config_data, self.temp_config)
        self.temp_config.close()
    
    def tearDown(self):
        """Clean up"""
        import os
        os.unlink(self.temp_config.name)
    
    def test_load_config(self):
        """Test loading configuration from file"""
        settings = Settings()
        settings.config_path = Path(self.temp_config.name)
        config = settings.load_config()
        
        self.assertIsNotNone(config)
        self.assertIn('rotation', config)
        self.assertIn('logging', config)
    
    def test_get_nested_value(self):
        """Test getting nested configuration values"""
        settings = Settings()
        settings.config_path = Path(self.temp_config.name)
        settings.config = settings.load_config()
        
        value = settings.get('rotation.schedule')
        self.assertEqual(value, 'daily')
        
        value = settings.get('logging.level')
        self.assertEqual(value, 'INFO')
    
    def test_get_with_default(self):
        """Test getting non-existent value with default"""
        settings = Settings()
        settings.config_path = Path(self.temp_config.name)
        settings.config = settings.load_config()
        
        value = settings.get('nonexistent.key', 'default_value')
        self.assertEqual(value, 'default_value')
    
    def test_get_deep_nested_value(self):
        """Test getting deeply nested values"""
        settings = Settings()
        settings.config_path = Path(self.temp_config.name)
        settings.config = settings.load_config()
        
        value = settings.get('providers.file_storage.file_path')
        self.assertEqual(value, 'data/secrets.json')
    
    def test_missing_config_file(self):
        """Test handling of missing config file"""
        settings = Settings()
        settings.config_path = Path('nonexistent_config.yaml')
        config = settings.load_config()
        
        # Should return empty dict without crashing
        self.assertEqual(config, {})
    
    def test_invalid_yaml_file(self):
        """Test handling of invalid YAML"""
        temp_invalid = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
        temp_invalid.write('invalid: yaml: content: [[[')
        temp_invalid.close()
        
        try:
            settings = Settings()
            settings.config_path = Path(temp_invalid.name)
            config = settings.load_config()
            
            # Should return empty dict without crashing
            self.assertEqual(config, {})
        finally:
            import os
            os.unlink(temp_invalid.name)
    
    def test_get_returns_none_for_partial_path(self):
        """Test that partial path returns default"""
        settings = Settings()
        settings.config_path = Path(self.temp_config.name)
        settings.config = settings.load_config()
        
        # 'rotation.schedule.subkey' doesn't exist (schedule is a string, not dict)
        value = settings.get('rotation.schedule.subkey', 'default')
        self.assertEqual(value, 'default')


if __name__ == '__main__':
    unittest.main()
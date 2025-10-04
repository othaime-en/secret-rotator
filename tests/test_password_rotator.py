import unittest
import sys
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from rotators.password_rotator import PasswordRotator

class TestPasswordRotator(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.config = {
            "length": 12,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        self.rotator = PasswordRotator("test_rotator", self.config)
    
    def test_generate_password_length(self):
        """Test that generated password has correct length"""
        password = self.rotator.generate_new_secret()
        self.assertEqual(len(password), 12)
    
    def test_generate_password_contains_all_types(self):
        """Test that password contains all required character types"""
        password = self.rotator.generate_new_secret()
        
        # Check for lowercase
        self.assertTrue(any(c.islower() for c in password))
        # Check for uppercase
        self.assertTrue(any(c.isupper() for c in password))
        # Check for numbers
        self.assertTrue(any(c.isdigit() for c in password))
        # Check for symbols
        self.assertTrue(any(c in "!@#$%^&*" for c in password))
    
    def test_validate_secret(self):
        """Test secret validation"""
        valid_password = "Test123!@#"
        self.assertTrue(self.rotator.validate_secret(valid_password))
        
        # Test invalid password (too short)
        invalid_password = "Test1!"
        self.assertFalse(self.rotator.validate_secret(invalid_password))

if __name__ == '__main__':
    unittest.main()
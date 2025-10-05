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
        self.assertTrue(any(c.islower() for c in password), "Password missing lowercase characters")
        self.assertTrue(any(c.isupper() for c in password), "Password missing uppercase characters")
        self.assertTrue(any(c.isdigit() for c in password), "Password missing digits")
        self.assertTrue(any(c in "!@#$%^&*" for c in password), "Password missing symbols")
    
    def test_validate_secret_valid_password(self):
        """Test validation of a valid password"""
        valid_password = "Ab1!kLmNpQrS"
        self.assertTrue(self.rotator.validate_secret(valid_password))
    
    def test_validate_secret_too_short(self):
        """Test validation of a password that is too short"""
        invalid_password = "Ab1!k3"
        self.assertFalse(self.rotator.validate_secret(invalid_password))
    
    def test_validate_secret_missing_lowercase(self):
        """Test validation of a password missing lowercase characters"""
        invalid_password = "AB1!KLMNPQRS"
        self.assertFalse(self.rotator.validate_secret(invalid_password))
    
    def test_validate_secret_empty_input(self):
        """Test validation of empty or None input"""
        self.assertFalse(self.rotator.validate_secret(""))
        self.assertFalse(self.rotator.validate_secret(None))
    
    def test_no_character_types_config(self):
        """Test validation when no character types are allowed in config"""
        config = {
            "length": 12,
            "use_lowercase": False,
            "use_uppercase": False,
            "use_numbers": False,
            "use_symbols": False
        }
        rotator = PasswordRotator("test_no_types", config)
        self.assertFalse(rotator.validate_secret("Ab1!kLmNpQrS"))

if __name__ == '__main__':
    unittest.main()
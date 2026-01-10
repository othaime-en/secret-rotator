import unittest
import sys
from pathlib import Path
from secret_rotator.rotators.password_rotator import PasswordRotator

class TestPasswordRotatorDeterministic(unittest.TestCase):
    """Test that password generation is deterministic and always valid"""
    
    def test_generate_always_validates_all_types(self):
        """Test that generated passwords ALWAYS pass validation (run 100 times)"""
        config = {
            "length": 16,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        rotator = PasswordRotator("test_rotator", config)
        
        # Run 100 iterations to ensure it's truly deterministic
        for i in range(100):
            password = rotator.generate_new_secret()
            is_valid = rotator.validate_secret(password)
            
            # If this fails, print detailed info for debugging
            if not is_valid:
                assessment = rotator.get_strength_assessment(password)
                self.fail(
                    f"Iteration {i+1}: Generated password failed validation\n"
                    f"Password: {password}\n"
                    f"Assessment: {assessment}"
                )
            
            self.assertTrue(is_valid, f"Iteration {i+1} failed")
    
    def test_generate_always_validates_three_types(self):
        """Test with only 3 character types enabled"""
        config = {
            "length": 12,
            "use_symbols": False,  # Symbols disabled
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        rotator = PasswordRotator("test_rotator", config)
        
        for i in range(50):
            password = rotator.generate_new_secret()
            self.assertTrue(rotator.validate_secret(password), f"Iteration {i+1} failed")
            # Ensure no symbols are present
            self.assertFalse(any(c in "!@#$%^&*" for c in password))
    
    def test_generate_always_validates_two_types(self):
        """Test with only 2 character types enabled"""
        config = {
            "length": 10,
            "use_symbols": False,
            "use_numbers": False,
            "use_uppercase": True,
            "use_lowercase": True
        }
        rotator = PasswordRotator("test_rotator", config)
        
        for i in range(50):
            password = rotator.generate_new_secret()
            self.assertTrue(rotator.validate_secret(password), f"Iteration {i+1} failed")
    
    def test_generate_minimum_length_equals_types(self):
        """Test when length exactly equals number of required types"""
        config = {
            "length": 4,  # Exactly 4 types enabled
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        rotator = PasswordRotator("test_rotator", config)
        
        for i in range(50):
            password = rotator.generate_new_secret()
            self.assertEqual(len(password), 4)
            self.assertTrue(rotator.validate_secret(password), f"Iteration {i+1} failed")
    
    def test_each_generated_password_contains_all_required_types(self):
        """Explicitly verify each character type is present"""
        config = {
            "length": 20,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        rotator = PasswordRotator("test_rotator", config)
        
        for i in range(100):
            password = rotator.generate_new_secret()
            
            has_lower = any(c.islower() for c in password)
            has_upper = any(c.isupper() for c in password)
            has_digit = any(c.isdigit() for c in password)
            has_symbol = any(c in "!@#$%^&*" for c in password)
            
            self.assertTrue(has_lower, f"Iteration {i+1}: Missing lowercase in {password}")
            self.assertTrue(has_upper, f"Iteration {i+1}: Missing uppercase in {password}")
            self.assertTrue(has_digit, f"Iteration {i+1}: Missing digit in {password}")
            self.assertTrue(has_symbol, f"Iteration {i+1}: Missing symbol in {password}")


class TestPasswordRotatorValidation(unittest.TestCase):
    """Test validation logic"""
    
    def setUp(self):
        self.config = {
            "length": 12,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        self.rotator = PasswordRotator("test_rotator", self.config)
    
    def test_validate_correct_password(self):
        """Test validation of a correct password"""
        valid_password = "Ab1!kLmNpQrS"
        self.assertTrue(self.rotator.validate_secret(valid_password))
    
    def test_validate_too_short(self):
        """Test validation of a password that is too short"""
        invalid_password = "Ab1!k3"
        self.assertFalse(self.rotator.validate_secret(invalid_password))
    
    def test_validate_missing_lowercase(self):
        """Test validation of a password missing lowercase characters"""
        invalid_password = "AB1!KLMNPQRS"
        self.assertFalse(self.rotator.validate_secret(invalid_password))
    
    def test_validate_missing_uppercase(self):
        """Test validation of a password missing uppercase characters"""
        invalid_password = "ab1!klmnpqrs"
        self.assertFalse(self.rotator.validate_secret(invalid_password))
    
    def test_validate_missing_numbers(self):
        """Test validation of a password missing numbers"""
        invalid_password = "Abcd!efghijk"
        self.assertFalse(self.rotator.validate_secret(invalid_password))
    
    def test_validate_missing_symbols(self):
        """Test validation of a password missing symbols"""
        invalid_password = "Ab1cdEfghijk"
        self.assertFalse(self.rotator.validate_secret(invalid_password))
    
    def test_validate_empty_input(self):
        """Test validation of empty or None input"""
        self.assertFalse(self.rotator.validate_secret(""))
        self.assertFalse(self.rotator.validate_secret(None))
    
    def test_validate_invalid_characters(self):
        """Test validation with characters not in any pool"""
        invalid_password = "Ab1!test£€¥"  # Contains £, €, ¥
        self.assertFalse(self.rotator.validate_secret(invalid_password))
    
    def test_validate_no_character_types_enabled(self):
        """Test validation when no character types are enabled"""
        config = {
            "length": 12,
            "use_lowercase": False,
            "use_uppercase": False,
            "use_numbers": False,
            "use_symbols": False
        }
        rotator = PasswordRotator("test_no_types", config)
        self.assertFalse(rotator.validate_secret("anypassword"))


class TestPasswordRotatorAmbiguousChars(unittest.TestCase):
    """Test ambiguous character exclusion"""
    
    def test_exclude_ambiguous_characters(self):
        """Test that ambiguous characters are excluded when configured"""
        config = {
            "length": 20,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True,
            "exclude_ambiguous": True
        }
        rotator = PasswordRotator("test_rotator", config)
        
        ambiguous = set('il1Lo0O')
        
        for i in range(100):
            password = rotator.generate_new_secret()
            for char in password:
                self.assertNotIn(
                    char, ambiguous,
                    f"Iteration {i+1}: Found ambiguous char '{char}' in {password}"
                )
            # Should still validate
            self.assertTrue(rotator.validate_secret(password))
    
    def test_validation_rejects_ambiguous_when_excluded(self):
        """Test that validation rejects passwords with ambiguous chars when excluded"""
        config = {
            "length": 12,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True,
            "exclude_ambiguous": True
        }
        rotator = PasswordRotator("test_rotator", config)
        
        # Password with ambiguous characters
        password_with_ambiguous = "Ab1!test0O1l"
        self.assertFalse(rotator.validate_secret(password_with_ambiguous))


class TestPasswordRotatorEntropy(unittest.TestCase):
    """Test entropy calculation"""
    
    def test_calculate_entropy_all_types(self):
        """Test entropy calculation with all character types"""
        config = {
            "length": 16,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        rotator = PasswordRotator("test_rotator", config)
        password = rotator.generate_new_secret()
        entropy = rotator.calculate_entropy(password)
        
        # With all types, pool size = 26+26+10+8 = 70
        # Entropy should be around 16 * log2(70) ≈ 98 bits
        self.assertGreater(entropy, 90)
        self.assertLess(entropy, 110)
    
    def test_calculate_entropy_empty_password(self):
        """Test entropy of empty password"""
        config = {"length": 16}
        rotator = PasswordRotator("test_rotator", config)
        self.assertEqual(rotator.calculate_entropy(""), 0.0)
    
    def test_strength_assessment(self):
        """Test strength assessment function"""
        config = {
            "length": 16,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        rotator = PasswordRotator("test_rotator", config)
        password = rotator.generate_new_secret()
        assessment = rotator.get_strength_assessment(password)
        
        self.assertIn('length', assessment)
        self.assertIn('entropy_bits', assessment)
        self.assertIn('strength', assessment)
        self.assertIn('meets_requirements', assessment)
        
        self.assertEqual(assessment['length'], 16)
        self.assertTrue(assessment['has_lowercase'])
        self.assertTrue(assessment['has_uppercase'])
        self.assertTrue(assessment['has_numbers'])
        self.assertTrue(assessment['has_symbols'])
        self.assertTrue(assessment['meets_requirements'])
        self.assertIn(assessment['strength'], ['strong', 'very_strong'])


class TestPasswordRotatorEdgeCases(unittest.TestCase):
    """Test edge cases and error conditions"""
    
    def test_generate_length_too_short_for_types(self):
        """Test when length is less than number of required types"""
        config = {
            "length": 2,  # Only 2 chars, but 4 types required
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        rotator = PasswordRotator("test_rotator", config)
        password = rotator.generate_new_secret()
        
        # Should return empty string or handle gracefully
        self.assertEqual(password, "")
    
    def test_generate_no_types_enabled(self):
        """Test when no character types are enabled"""
        config = {
            "length": 12,
            "use_symbols": False,
            "use_numbers": False,
            "use_uppercase": False,
            "use_lowercase": False
        }
        rotator = PasswordRotator("test_rotator", config)
        password = rotator.generate_new_secret()
        
        self.assertEqual(password, "")
    
    def test_very_long_password(self):
        """Test generating very long passwords"""
        config = {
            "length": 256,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        rotator = PasswordRotator("test_rotator", config)
        
        password = rotator.generate_new_secret()
        self.assertEqual(len(password), 256)
        self.assertTrue(rotator.validate_secret(password))
    
    def test_password_uniqueness(self):
        """Test that multiple generated passwords are unique"""
        config = {
            "length": 16,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        rotator = PasswordRotator("test_rotator", config)
        
        passwords = [rotator.generate_new_secret() for _ in range(100)]
        
        # All passwords should be unique
        unique_passwords = set(passwords)
        self.assertEqual(len(unique_passwords), 100)


class TestPasswordRotatorIntegration(unittest.TestCase):
    """Integration tests simulating real rotation scenarios"""
    
    def test_rotation_workflow(self):
        """Test complete rotation workflow"""
        config = {
            "length": 16,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        rotator = PasswordRotator("production_rotator", config)
        
        # Generate 10 passwords as if rotating 10 different secrets
        for i in range(10):
            password = rotator.generate_new_secret()
            
            # Each must be valid
            self.assertTrue(rotator.validate_secret(password))
            
            # Each must meet length requirement
            self.assertEqual(len(password), 16)
            
            # Get strength assessment
            assessment = rotator.get_strength_assessment(password)
            self.assertIn(assessment['strength'], ['strong', 'very_strong'])
    
    def test_backward_compatibility_validation(self):
        """Test that externally generated passwords can be validated"""
        config = {
            "length": 12,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True
        }
        rotator = PasswordRotator("test_rotator", config)
        
        # Simulate passwords from external sources
        external_valid = "MyP@ssw0rd123"
        external_invalid = "weakpass"
        
        self.assertTrue(rotator.validate_secret(external_valid))
        self.assertFalse(rotator.validate_secret(external_invalid))


if __name__ == '__main__':
    # Run tests with verbose output
    unittest.main(verbosity=2)
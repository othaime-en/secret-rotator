import secrets
import string
from typing import Dict, Any
from .base import SecretRotator
from utils.logger import logger

class PasswordRotator(SecretRotator):
    """Generate random passwords"""
    
    # Define allowed symbols as a class attribute for consistency
    ALLOWED_SYMBOLS = "!@#$%^&*"

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.length = config.get('length', 16)
        self.use_symbols = config.get('use_symbols', True)
        self.use_numbers = config.get('use_numbers', True)
        self.use_uppercase = config.get('use_uppercase', True)
        self.use_lowercase = config.get('use_lowercase', True)
        # Cache allowed symbols as a set for O(1) lookup
        self._symbol_set = set(self.ALLOWED_SYMBOLS)

    def generate_new_secret(self) -> str:
        """Generate a new random password"""
        characters = ""
        
        if self.use_lowercase:
            characters += string.ascii_lowercase
        if self.use_uppercase:
            characters += string.ascii_uppercase
        if self.use_numbers:
            characters += string.digits
        if self.use_symbols:
            characters += self.ALLOWED_SYMBOLS
        
        if not characters:
            logger.error("No character types selected for password generation")
            return ""
        
        # Generate password
        password = ''.join(secrets.choice(characters) for _ in range(self.length))
        logger.info(f"Generated new password of length {len(password)}")
        return password
    
    def validate_secret(self, secret: str) -> bool:
        """Validate password meets requirements"""
        # Check for None or empty input
        if not isinstance(secret, str) or not secret:
            logger.error("Invalid secret: must be a non-empty string")
            return False
        
        # Check length requirement
        if len(secret) < self.length:
            logger.warning(f"Secret length {len(secret)} is less than required {self.length}")
            return False
        
        # Check if any character types are enabled
        if not (self.use_lowercase or self.use_uppercase or self.use_numbers or self.use_symbols):
            logger.error("No character types enabled for validation")
            return False
        
        # Validate character type requirements
        checks = []
        if self.use_lowercase:
            checks.append((any(c.islower() for c in secret), "lowercase"))
        if self.use_uppercase:
            checks.append((any(c.isupper() for c in secret), "uppercase"))
        if self.use_numbers:
            checks.append((any(c.isdigit() for c in secret), "digits"))
        if self.use_symbols:
            checks.append((any(c in self._symbol_set for c in secret), "symbols"))
        
        # Log specific validation failures
        for check_passed, check_type in checks:
            if not check_passed:
                logger.warning(f"Secret validation failed: missing {check_type} characters")
        
        return all(check[0] for check in checks)
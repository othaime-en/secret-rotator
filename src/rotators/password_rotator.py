import secrets
import string
from typing import Dict, Any
from .base import SecretRotator
from src.utils.logger import logger

class PasswordRotator(SecretRotator):
    """Generate random passwords"""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.length = config.get('length', 16)
        self.use_symbols = config.get('use_symbols', True)
        self.use_numbers = config.get('use_numbers', True)
        self.use_uppercase = config.get('use_uppercase', True)
        self.use_lowercase = config.get('use_lowercase', True)
    
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
            characters += "!@#$%^&*"
        
        if not characters:
            logger.error("No character types selected for password generation")
            return ""
        
        # Generate password
        password = ''.join(secrets.choice(characters) for _ in range(self.length))
        logger.info(f"Generated new password of length {len(password)}")
        return password
    
    def validate_secret(self, secret: str) -> bool:
        """Validate password meets requirements"""
        if len(secret) < self.length:
            return False
        
        checks = []
        if self.use_lowercase:
            checks.append(any(c.islower() for c in secret))
        if self.use_uppercase:
            checks.append(any(c.isupper() for c in secret))
        if self.use_numbers:
            checks.append(any(c.isdigit() for c in secret))
        if self.use_symbols:
            checks.append(any(c in "!@#$%^&*" for c in secret))
        
        return all(checks)
"""
Advanced secret rotators for different secret types.
"""
import secrets
import string
from typing import Dict, Any
from .base import SecretRotator
from utils.logger import logger

class DatabasePasswordRotator(SecretRotator):
    """
    Rotate database passwords with connection testing.
    Supports MySQL, PostgreSQL, MongoDB, etc.
    """

    plugin_name = "database_password"

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.db_type = config.get('db_type', 'postgresql')
        self.host = config.get('host', 'localhost')
        self.port = config.get('port')
        self.database = config.get('database')
        self.username = config.get('username')
        self.length = config.get('length', 32)
        self.test_connection = config.get('test_connection', True)

    def generate_new_secret(self) -> str:
        """Generate a strong database password"""
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        password = ''.join(secrets.choice(chars) for _ in range(self.length))

        # Ensure it starts with a letter (some DBs require this)
        if not password[0].isalpha():
            password = secrets.choice(string.ascii_letters) + password[1:]

        logger.info(f"Generated new {self.db_type} password")
        return password

    def validate_secret(self, secret: str) -> bool:
        """Validate password meets database requirements"""
        if len(secret) < 12:
            return False

        # Check complexity
        has_upper = any(c.isupper() for c in secret)
        has_lower = any(c.islower() for c in secret)
        has_digit = any(c.isdigit() for c in secret)

        if not (has_upper and has_lower and has_digit):
            return False

        # Test connection if configured
        if self.test_connection:
            return self._test_database_connection(secret)

        return True
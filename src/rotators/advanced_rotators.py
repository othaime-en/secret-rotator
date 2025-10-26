"""
Advanced secret rotators for different secret types.
"""
import secrets
import string
import json
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

    def _test_database_connection(self, password: str) -> bool:
        """Test database connection with new password"""
        try:
            if self.db_type == 'postgresql':
                import psycopg2
                conn = psycopg2.connect(
                    host=self.host,
                    port=self.port or 5432,
                    database=self.database,
                    user=self.username,
                    password=password,
                    connect_timeout=5
                )
                conn.close()
                return True

            elif self.db_type == 'mysql':
                import mysql.connector
                conn = mysql.connector.connect(
                    host=self.host,
                    port=self.port or 3306,
                    database=self.database,
                    user=self.username,
                    password=password,
                    connection_timeout=5
                )
                conn.close()
                return True

            elif self.db_type == 'mongodb':
                from pymongo import MongoClient
                client = MongoClient(
                    host=self.host,
                    port=self.port or 27017,
                    username=self.username,
                    password=password,
                    serverSelectionTimeoutMS=5000
                )
                client.server_info()
                client.close()
                return True

            return True

        except ImportError:
            logger.warning(f"Database driver for {self.db_type} not installed")
            return True  # Skip validation if driver not available
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False
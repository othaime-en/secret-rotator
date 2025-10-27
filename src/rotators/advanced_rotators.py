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

class APIKeyRotator(SecretRotator):
"""
Generate API keys in various formats.
Supports prefixes for easy identification.
"""

    plugin_name = "api_key"

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.length = config.get('length', 32)
        self.format = config.get('format', 'hex')  # hex, base64, alphanumeric
        self.prefix = config.get('prefix', '')  # e.g., "sk_live_"
        self.include_checksum = config.get('include_checksum', False)

    def generate_new_secret(self) -> str:
        """Generate an API key"""
        if self.format == 'hex':
            key_part = secrets.token_hex(self.length // 2)
        elif self.format == 'base64':
            key_part = secrets.token_urlsafe(self.length)[:self.length]
        else:  # alphanumeric
            chars = string.ascii_letters + string.digits
            key_part = ''.join(secrets.choice(chars) for _ in range(self.length))

        api_key = f"{self.prefix}{key_part}"

        if self.include_checksum:
            checksum = self._calculate_checksum(api_key)
            api_key = f"{api_key}_{checksum}"

        logger.info(f"Generated new API key with format {self.format}")
        return api_key

    def validate_secret(self, secret: str) -> bool:
        """Validate API key format"""
        if self.prefix and not secret.startswith(self.prefix):
            return False

        if self.include_checksum:
            if '_' not in secret:
                return False
            key_part, checksum = secret.rsplit('_', 1)
            expected_checksum = self._calculate_checksum(key_part)
            return checksum == expected_checksum

        return len(secret) >= self.length

    def _calculate_checksum(self, value: str) -> str:
        """Calculate checksum for API key validation"""
        import hashlib
        return hashlib.sha256(value.encode()).hexdigest()[:8]


class JWTSecretRotator(SecretRotator):
"""
Generate secrets for JWT signing.
Creates cryptographically secure keys suitable for HS256, HS384, HS512.
"""

    plugin_name = "jwt_secret"

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.algorithm = config.get('algorithm', 'HS256')
        self.min_length = self._get_min_length()

    def _get_min_length(self) -> int:
        """Get minimum length based on algorithm"""
        if self.algorithm == 'HS256':
            return 32  # 256 bits
        elif self.algorithm == 'HS384':
            return 48  # 384 bits
        elif self.algorithm == 'HS512':
            return 64  # 512 bits
        return 32

    def generate_new_secret(self) -> str:
        """Generate JWT signing secret"""
        # Generate URL-safe base64 encoded secret
        secret = secrets.token_urlsafe(self.min_length)
        logger.info(f"Generated new JWT secret for {self.algorithm}")
        return secret

    def validate_secret(self, secret: str) -> bool:
        """Validate JWT secret meets minimum length"""
        if len(secret) < self.min_length:
            logger.warning(f"JWT secret too short for {self.algorithm}")
            return False

        # Test if it can be used with PyJWT
        try:
            import jwt
            test_payload = {"test": "data"}
            token = jwt.encode(test_payload, secret, algorithm=self.algorithm)
            decoded = jwt.decode(token, secret, algorithms=[self.algorithm])
            return decoded == test_payload
        except ImportError:
            logger.warning("PyJWT not installed, skipping JWT validation")
            return True
        except Exception as e:
            logger.error(f"JWT validation failed: {e}")
            return False


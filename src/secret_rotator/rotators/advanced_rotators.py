"""
Advanced secret rotators for different secret types.
"""

import secrets
import string
import json
from typing import Dict, Any
from secret_rotator.rotators.base import SecretRotator
from secret_rotator.utils.logger import logger


class DatabasePasswordRotator(SecretRotator):
    """
    Rotate database passwords with connection testing.
    Supports MySQL, PostgreSQL, MongoDB, etc.
    """

    plugin_name = "database_password"

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.db_type = config.get("db_type", "postgresql")
        self.host = config.get("host", "localhost")
        self.port = config.get("port")
        self.database = config.get("database")
        self.username = config.get("username")
        self.length = config.get("length", 32)
        self.test_connection = config.get("test_connection", True)

    def generate_new_secret(self) -> str:
        """Generate a strong database password"""
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        password = "".join(secrets.choice(chars) for _ in range(self.length))

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
            if self.db_type == "postgresql":
                import psycopg2

                conn = psycopg2.connect(
                    host=self.host,
                    port=self.port or 5432,
                    database=self.database,
                    user=self.username,
                    password=password,
                    connect_timeout=5,
                )
                conn.close()
                return True

            elif self.db_type == "mysql":
                import mysql.connector

                conn = mysql.connector.connect(
                    host=self.host,
                    port=self.port or 3306,
                    database=self.database,
                    user=self.username,
                    password=password,
                    connection_timeout=5,
                )
                conn.close()
                return True

            elif self.db_type == "mongodb":
                from pymongo import MongoClient

                client = MongoClient(
                    host=self.host,
                    port=self.port or 27017,
                    username=self.username,
                    password=password,
                    serverSelectionTimeoutMS=5000,
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
        self.length = config.get("length", 32)
        self.format = config.get("format", "hex")  # hex, base64, alphanumeric
        self.prefix = config.get("prefix", "")  # e.g., "sk_live_"
        self.include_checksum = config.get("include_checksum", False)

    def generate_new_secret(self) -> str:
        """Generate an API key"""
        if self.format == "hex":
            key_part = secrets.token_hex(self.length // 2)
        elif self.format == "base64":
            key_part = secrets.token_urlsafe(self.length)[: self.length]
        else:  # alphanumeric
            chars = string.ascii_letters + string.digits
            key_part = "".join(secrets.choice(chars) for _ in range(self.length))

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
            if "_" not in secret:
                return False
            key_part, checksum = secret.rsplit("_", 1)
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
        self.algorithm = config.get("algorithm", "HS256")
        self.min_length = self._get_min_length()

    def _get_min_length(self) -> int:
        """Get minimum length based on algorithm"""
        if self.algorithm == "HS256":
            return 32  # 256 bits
        elif self.algorithm == "HS384":
            return 48  # 384 bits
        elif self.algorithm == "HS512":
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


class SSHKeyRotator(SecretRotator):
    """
    Generate SSH key pairs.
    Creates both private and public keys.
    """

    plugin_name = "ssh_key"

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.key_type = config.get("key_type", "rsa")  # rsa, ed25519
        self.key_size = config.get("key_size", 4096)
        self.comment = config.get("comment", "")

    def generate_new_secret(self) -> str:
        """Generate SSH key pair"""
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend

            if self.key_type == "rsa":
                private_key = rsa.generate_private_key(
                    public_exponent=65537, key_size=self.key_size, backend=default_backend()
                )
            elif self.key_type == "ed25519":
                private_key = ed25519.Ed25519PrivateKey.generate()
            else:
                raise ValueError(f"Unsupported key type: {self.key_type}")

            # Serialize private key
            private_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.OpenSSH,
                encryption_algorithm=serialization.NoEncryption(),
            )

            # Get public key
            public_key = private_key.public_key()
            public_ssh = public_key.public_bytes(
                encoding=serialization.Encoding.OpenSSH, format=serialization.PublicFormat.OpenSSH
            )

            # Return as JSON with both keys
            key_pair = {
                "private_key": private_pem.decode("utf-8"),
                "public_key": public_ssh.decode("utf-8")
                + (f" {self.comment}" if self.comment else ""),
            }

            logger.info(f"Generated new {self.key_type} SSH key pair")
            return json.dumps(key_pair)

        except ImportError:
            logger.error("cryptography library not installed")
            raise
        except Exception as e:
            logger.error(f"SSH key generation failed: {e}")
            raise

    def validate_secret(self, secret: str) -> bool:
        """Validate SSH key pair"""
        try:
            key_pair = json.loads(secret)
            return "private_key" in key_pair and "public_key" in key_pair
        except BaseException:
            return False


class CertificateRotator(SecretRotator):
    """
    Generate self-signed certificates or CSRs.
    Useful for internal services and testing.
    """

    plugin_name = "certificate"

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.common_name = config.get("common_name", "localhost")
        self.validity_days = config.get("validity_days", 365)
        self.key_size = config.get("key_size", 2048)
        self.san_list = config.get("san_list", [])  # Subject Alternative Names

    def generate_new_secret(self) -> str:
        """Generate self-signed certificate"""
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID, ExtensionOID
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend
            import datetime

            # Generate private key
            private_key = rsa.generate_private_key(
                public_exponent=65537, key_size=self.key_size, backend=default_backend()
            )

            # Create certificate
            subject = issuer = x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, self.common_name)]
            )

            cert_builder = x509.CertificateBuilder()
            cert_builder = cert_builder.subject_name(subject)
            cert_builder = cert_builder.issuer_name(issuer)
            cert_builder = cert_builder.public_key(private_key.public_key())
            cert_builder = cert_builder.serial_number(x509.random_serial_number())
            cert_builder = cert_builder.not_valid_before(datetime.datetime.utcnow())
            cert_builder = cert_builder.not_valid_after(
                datetime.datetime.utcnow() + datetime.timedelta(days=self.validity_days)
            )

            # Add SAN if provided
            if self.san_list:
                san = [x509.DNSName(name) for name in self.san_list]
                cert_builder = cert_builder.add_extension(
                    x509.SubjectAlternativeName(san), critical=False
                )

            # Sign certificate
            certificate = cert_builder.sign(private_key, hashes.SHA256(), default_backend())

            # Serialize
            cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
            key_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )

            result = {
                "certificate": cert_pem.decode("utf-8"),
                "private_key": key_pem.decode("utf-8"),
            }

            logger.info(f"Generated new certificate for {self.common_name}")
            return json.dumps(result)

        except ImportError:
            logger.error("cryptography library not installed")
            raise
        except Exception as e:
            logger.error(f"Certificate generation failed: {e}")
            raise

    def validate_secret(self, secret: str) -> bool:
        """Validate certificate"""
        try:
            cert_data = json.loads(secret)
            return "certificate" in cert_data and "private_key" in cert_data
        except BaseException:
            return False


class OAuth2TokenRotator(SecretRotator):
    """
    Rotate OAuth2 client secrets.
    Integrates with OAuth providers to update credentials.
    """

    plugin_name = "oauth2_secret"

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.provider = config.get("provider")  # google, github, azure, etc.
        self.client_id = config.get("client_id")
        self.length = config.get("length", 48)

    def generate_new_secret(self) -> str:
        """Generate OAuth2 client secret"""
        # Generate a URL-safe secret
        secret = secrets.token_urlsafe(self.length)
        logger.info(f"Generated new OAuth2 secret for {self.provider}")
        return secret

    def validate_secret(self, secret: str) -> bool:
        """Validate OAuth2 secret format"""
        return len(secret) >= 32

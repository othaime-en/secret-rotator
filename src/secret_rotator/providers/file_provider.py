import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from secret_rotator.providers.base import SecretProvider
from secret_rotator.utils.logger import logger
from secret_rotator.utils.retry import retry_with_backoff
from secret_rotator.encryption_manager import EncryptionManager


class FileSecretProvider(SecretProvider):
    """File-based secret storage with encryption support"""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.file_path = Path(config.get("file_path", "secrets.json"))
        self.encrypt_secrets = config.get("encrypt_secrets", True)

        # Initialize encryption manager if encryption is enabled
        self.encryption_manager = None
        if self.encrypt_secrets:
            key_file = config.get("encryption_key_file", "config/.master.key")
            self.encryption_manager = EncryptionManager(key_file=key_file)
            logger.info(f"Encryption enabled for provider {name}")

        self.ensure_file_exists()

    def ensure_file_exists(self):
        """Create secrets file if it doesn't exist"""
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, "w") as f:
                json.dump({}, f)

    @retry_with_backoff(
        max_attempts=3, initial_delay=0.5, exceptions=(IOError, json.JSONDecodeError)
    )
    def get_secret(self, secret_id: str) -> str:
        """Retrieve and decrypt a secret from file"""
        try:
            with open(self.file_path, "r") as f:
                secrets = json.load(f)
                encrypted_value = secrets.get(secret_id, "")

                if not encrypted_value:
                    return ""

                # Decrypt if encryption is enabled
                if self.encrypt_secrets and self.encryption_manager:
                    try:
                        decrypted_value = self.encryption_manager.decrypt(encrypted_value)
                        logger.debug(f"Successfully decrypted secret: {secret_id}")
                        return decrypted_value
                    except Exception as e:
                        logger.error(f"Failed to decrypt secret {secret_id}: {e}")
                        return ""

                # Return raw value if encryption is disabled
                return encrypted_value

        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.error(f"Error reading secrets file: {e}")
            return ""

    @retry_with_backoff(max_attempts=3, initial_delay=0.5, exceptions=(IOError,))
    def update_secret(self, secret_id: str, new_value: str) -> bool:
        """Encrypt and update secret in file"""
        try:
            # Read current secrets
            with open(self.file_path, "r") as f:
                secrets = json.load(f)

            # Encrypt the new value if encryption is enabled
            value_to_store = new_value
            if self.encrypt_secrets and self.encryption_manager:
                try:
                    value_to_store = self.encryption_manager.encrypt(new_value)
                    logger.debug(f"Successfully encrypted secret: {secret_id}")
                except Exception as e:
                    logger.error(f"Failed to encrypt secret {secret_id}: {e}")
                    return False

            # Update secret
            secrets[secret_id] = value_to_store

            # Write back to file
            with open(self.file_path, "w") as f:
                json.dump(secrets, f, indent=2)

            logger.info(f"Successfully updated secret: {secret_id}")
            return True

        except Exception as e:
            logger.error(f"Error updating secret {secret_id}: {e}")
            return False

    @retry_with_backoff(max_attempts=2, exceptions=(OSError,))
    def validate_connection(self) -> bool:
        """Test if file can be accessed and encryption is working"""
        try:
            # Check file access
            if not (self.file_path.exists() and os.access(self.file_path, os.R_OK | os.W_OK)):
                return False

            # Test encryption/decryption if enabled
            if self.encrypt_secrets and self.encryption_manager:
                test_value = "test_encryption"
                encrypted = self.encryption_manager.encrypt(test_value)
                decrypted = self.encryption_manager.decrypt(encrypted)

                if decrypted != test_value:
                    logger.error("Encryption validation failed")
                    return False

            return True

        except Exception as e:
            logger.error(f"Connection validation failed: {e}")
            return False

    def migrate_to_encrypted(self) -> bool:
        """
        Migrate existing plaintext secrets to encrypted format.
        This should be called once when enabling encryption on existing data.
        """
        if not self.encryption_manager:
            logger.error("Cannot migrate: encryption manager not initialized")
            return False

        try:
            with open(self.file_path, "r") as f:
                secrets = json.load(f)

            migrated_secrets = {}
            for secret_id, value in secrets.items():
                # Try to decrypt - if it fails, assume it's plaintext
                try:
                    self.encryption_manager.decrypt(value)
                    # Already encrypted, keep as is
                    migrated_secrets[secret_id] = value
                    logger.debug(f"Secret {secret_id} already encrypted")
                except BaseException:
                    # Not encrypted, encrypt it now
                    encrypted_value = self.encryption_manager.encrypt(value)
                    migrated_secrets[secret_id] = encrypted_value
                    logger.info(f"Migrated secret {secret_id} to encrypted format")

            # Write back encrypted secrets
            with open(self.file_path, "w") as f:
                json.dump(migrated_secrets, f, indent=2)

            logger.info(
                f"Successfully migrated {len(migrated_secrets)} secrets to encrypted format"
            )
            return True

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return False

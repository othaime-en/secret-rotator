"""
Encryption manager for securing secrets at rest and in backups.
Uses Fernet (symmetric encryption) from cryptography library.
"""

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC as PBKDF2
from cryptography.hazmat.backends import default_backend
import base64
import os
import json
import hashlib
import secrets
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from secret_rotator.utils.logger import logger
from datetime import datetime, timedelta


class EncryptionManager:
    """Handle encryption/decryption of secrets using a master key"""

    def __init__(self, key_file: str = "data/.master.key"):
        """
        Architecture Note (v1.2.0):
            Master key moved from config/ to data/ to enable:
            - Auto-generation on first run
            - Read-only config directory in production
            - Proper separation of config vs runtime data
        """
        self.key_file = Path(key_file)
        self.cipher = None
        self.key_metadata: Dict[str, Any] = {}
        self._initialize_encryption()

    def _initialize_encryption(self):
        """Initialize encryption cipher with master key"""
        if self.key_file.exists():
            key = self._load_existing_key()
            logger.info("Loaded existing master encryption key")
        else:
            key = self._generate_and_save_key()
            logger.info("Generated new master encryption key")

        self.cipher = Fernet(key)

    def _load_existing_key(self) -> bytes:
        """Load existing key from file with metadata validation"""
        try:
            with open(self.key_file, "r") as f:
                key_data = json.load(f)

            # Extract key and metadata
            key_str = key_data["key"]
            self.key_metadata = key_data.get("metadata", {})

            # Convert string back to bytes
            key_bytes = key_str.encode("utf-8")

            # Verify key integrity
            expected_key_id = self.key_metadata.get("key_id")
            if expected_key_id:
                actual_key_id = hashlib.sha256(key_bytes).hexdigest()[:16]
                if expected_key_id != actual_key_id:
                    raise ValueError("Master key integrity check failed")

            # Return the base64-encoded key bytes (what Fernet expects)
            return key_bytes

        except json.JSONDecodeError:
            # Handle legacy key files (raw bytes without metadata)
            logger.warning("Loading legacy key file without metadata")
            with open(self.key_file, "rb") as f:
                key = f.read()

            # Create metadata for legacy key
            self.key_metadata = {
                "version": 0,
                "algorithm": "Fernet",
                "key_id": hashlib.sha256(key).hexdigest()[:16],
                "legacy": True,
            }

            return key

    def _generate_and_save_key(self) -> bytes:
        """Generate a new encryption key and save it securely with metadata"""
        # Generate cryptographically secure random key
        key = Fernet.generate_key()  # Already base64-encoded bytes

        # Create metadata - use the key directly for checksum
        self.key_metadata = {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "algorithm": "Fernet",
            "key_id": hashlib.sha256(key).hexdigest()[:16],  # Hash the base64 bytes
        }

        # Package key with metadata
        key_data = {
            "key": key.decode("utf-8"),  # Just decode to string, don't double-encode
            "metadata": self.key_metadata,
        }

        # Create config directory if it doesn't exist
        self.key_file.parent.mkdir(parents=True, exist_ok=True)

        # Save key with metadata as JSON
        with open(self.key_file, "w") as f:
            json.dump(key_data, f, indent=2)

        # Set file permissions to 0600 (owner read/write only)
        os.chmod(self.key_file, 0o600)

        logger.warning(
            f"Master key generated at {self.key_file}. "
            "BACKUP THIS FILE SECURELY - it cannot be recovered if lost!"
        )

        return key

    def encrypt(self, plaintext: str, associated_data: Optional[Dict[str, Any]] = None) -> str:
        """
        Encrypt plaintext and return base64-encoded ciphertext.

        Args:
            plaintext: Data to encrypt
            associated_data: Optional metadata to include (stored separately, not encrypted)

        Returns:
            Base64-encoded ciphertext, or JSON with metadata if associated_data provided
        """
        if not plaintext:
            return ""

        try:
            encrypted_bytes = self.cipher.encrypt(plaintext.encode("utf-8"))
            ciphertext = base64.b64encode(encrypted_bytes).decode("utf-8")

            # If no associated data, return simple base64 string (backward compatible)
            if not associated_data:
                return ciphertext

            # If associated data provided, package with metadata
            package = {
                "ciphertext": ciphertext,
                "metadata": associated_data,
                "encrypted_at": datetime.now().isoformat(),
            }
            return json.dumps(package)

        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt base64-encoded ciphertext and return plaintext.

        Args:
            ciphertext: Base64-encoded ciphertext or JSON package with metadata

        Returns:
            Decrypted plaintext
        """
        if not ciphertext:
            return ""

        try:
            # Try to parse as JSON first (if it has associated data)
            try:
                package = json.loads(ciphertext)
                if "ciphertext" in package:
                    actual_ciphertext = package["ciphertext"]
                else:
                    actual_ciphertext = ciphertext
            except json.JSONDecodeError:
                # Not JSON, treat as raw base64 ciphertext
                actual_ciphertext = ciphertext

            # Decrypt
            encrypted_bytes = base64.b64decode(actual_ciphertext.encode("utf-8"))
            decrypted_bytes = self.cipher.decrypt(encrypted_bytes)
            return decrypted_bytes.decode("utf-8")

        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise

    def get_metadata(self, ciphertext: str) -> Optional[Dict[str, Any]]:
        """
        Extract metadata from encrypted package without decrypting.

        Args:
            ciphertext: Encrypted data (possibly with metadata)

        Returns:
            Metadata dict if present, None otherwise
        """
        try:
            package = json.loads(ciphertext)
            return package.get("metadata")
        except json.JSONDecodeError:
            return None

    def get_key_info(self) -> Dict[str, Any]:
        """
        Get information about the current master key (non-sensitive).

        Returns:
            Dictionary with key metadata
        """
        info = {
            "key_id": self.key_metadata.get("key_id"),
            "version": self.key_metadata.get("version"),
            "algorithm": self.key_metadata.get("algorithm"),
            "created_at": self.key_metadata.get("created_at"),
            "rotated_from": self.key_metadata.get("rotated_from"),
            "rotated_at": self.key_metadata.get("rotated_at"),
        }

        # Calculate age if creation date available
        if self.key_metadata.get("created_at"):
            try:
                created_at = datetime.fromisoformat(self.key_metadata["created_at"])
                age = datetime.now() - created_at
                info["age_days"] = age.days
            except BaseException:
                info["age_days"] = None

        return info

    def should_rotate_key(self, max_age_days: int = 90) -> bool:
        """
        Check if master key should be rotated based on age.

        Args:
            max_age_days: Maximum age in days before rotation recommended

        Returns:
            True if key should be rotated
        """
        # If no creation date, recommend rotation
        if not self.key_metadata.get("created_at"):
            logger.warning("Key has no creation date, rotation recommended")
            return True

        try:
            created_at = datetime.fromisoformat(self.key_metadata["created_at"])
            age = datetime.now() - created_at

            if age > timedelta(days=max_age_days):
                logger.info(
                    f"Key is {age.days} days old (max: {max_age_days}), rotation recommended"
                )
                return True

            return False

        except Exception as e:
            logger.error(f"Error checking key age: {e}")
            return True  # Err on the side of caution

    def rotate_master_key(self, new_key: Optional[bytes] = None, re_encrypt_callback=None) -> bool:
        """
        Rotate the master encryption key with backup and rollback support.

        CRITICAL: This requires re-encrypting ALL secrets with the new key.
        The callback function will be called to handle re-encryption of all secrets.

        Args:
            new_key: Optional new key (if None, generates random key)
            re_encrypt_callback: Function(old_cipher, new_cipher) -> bool
                                Called to re-encrypt all secrets

        Returns:
            True if rotation succeeded, False if failed (with rollback)
        """
        if not self.cipher:
            raise ValueError("No master key to rotate")

        logger.info("Starting master key rotation")

        # Save old cipher for re-encryption
        old_cipher = self.cipher

        # Generate or use provided new key
        if new_key is None:
            new_key = Fernet.generate_key()

        new_cipher = Fernet(new_key)

        # Create new metadata
        new_metadata = {
            "version": self.key_metadata.get("version", 0) + 1,
            "created_at": datetime.now().isoformat(),
            "algorithm": "Fernet",
            "key_id": hashlib.sha256(new_key).hexdigest()[:16],
            "rotated_from": self.key_metadata.get("key_id"),
            "rotated_at": datetime.now().isoformat(),
        }

        # Backup old key file
        backup_path = self.key_file.with_suffix(".key.backup")
        if self.key_file.exists():
            import shutil

            try:
                shutil.copy2(self.key_file, backup_path)
                logger.info(f"Backed up old key to {backup_path}")
            except Exception as e:
                logger.error(f"Failed to backup old key: {e}")
                return False

        try:
            # Re-encrypt all secrets if callback provided
            if re_encrypt_callback:
                logger.info("Re-encrypting all secrets with new key...")
                success = re_encrypt_callback(old_cipher, new_cipher)
                if not success:
                    raise Exception("Re-encryption callback failed")
                logger.info("All secrets re-encrypted successfully")
            else:
                logger.warning(
                    "No re-encryption callback provided. "
                    "Existing encrypted data will become unreadable!"
                )

            # Update in-memory cipher and metadata
            self.cipher = new_cipher
            self.key_metadata = new_metadata

            # Save new key with metadata
            key_data = {"key": new_key.decode("utf-8"), "metadata": new_metadata}

            with open(self.key_file, "w") as f:
                json.dump(key_data, f, indent=2)

            os.chmod(self.key_file, 0o600)

            logger.info("Master key rotation completed successfully")
            logger.info(f"New key ID: {new_metadata['key_id']}")

            return True

        except Exception as e:
            logger.error(f"Master key rotation failed: {e}")
            logger.info("Restoring old key from backup...")

            # Restore from backup
            if backup_path.exists():
                import shutil

                try:
                    shutil.copy2(backup_path, self.key_file)
                    # Reload the old key
                    key = self._load_existing_key()
                    self.cipher = Fernet(key)
                    logger.info("Successfully restored old key")
                except Exception as restore_error:
                    logger.critical(f"Failed to restore old key: {restore_error}")
                    raise
            else:
                logger.critical("No backup found to restore!")
                raise

            return False

    @staticmethod
    def derive_key_from_passphrase(
        passphrase: str,
        salt: Optional[bytes] = None,
        iterations: int = 600000,  # OWASP 2023 recommendation
    ) -> Dict[str, str]:
        """
        Derive an encryption key from a passphrase using PBKDF2.
        Useful for environments where you can't store a key file.

        IMPORTANT: You MUST store the returned salt! Without it, the key cannot be derived again.

        Args:
            passphrase: User passphrase to derive key from
            salt: Salt for key derivation (if None, generates random salt)
            iterations: Number of PBKDF2 iterations (default: 600,000)

        Returns:
            Dictionary containing:
            - key: Base64-encoded derived key (ready for Fernet)
            - salt: Base64-encoded salt (MUST BE STORED)
            - iterations: Number of iterations used
            - algorithm: Algorithm used for derivation
        """
        if salt is None:
            salt = secrets.token_bytes(32)  # 256 bits

        kdf = PBKDF2(
            algorithm=hashes.SHA256(),
            length=32,  # Fernet requires 32 bytes
            salt=salt,
            iterations=iterations,
            backend=default_backend(),
        )

        # Derive key and encode for Fernet
        derived_key = kdf.derive(passphrase.encode("utf-8"))
        fernet_key = base64.urlsafe_b64encode(derived_key)

        return {
            "key": fernet_key.decode("utf-8"),
            "salt": base64.b64encode(salt).decode("utf-8"),
            "iterations": iterations,
            "algorithm": "PBKDF2-SHA256",
        }

    @staticmethod
    def create_from_passphrase(passphrase: str, salt: bytes) -> "EncryptionManager":
        """
        Create an EncryptionManager from a passphrase (without key file).

        Args:
            passphrase: User passphrase
            salt: Salt used during key derivation (must be same as original)

        Returns:
            Configured EncryptionManager instance
        """
        key_data = EncryptionManager.derive_key_from_passphrase(passphrase, salt)

        # Create instance without key file
        manager = EncryptionManager.__new__(EncryptionManager)
        manager.key_file = None
        manager.key_metadata = {
            "version": 1,
            "algorithm": "Fernet",
            "derived_from": "passphrase",
            "iterations": key_data["iterations"],
        }

        # Initialize cipher with derived key
        manager.cipher = Fernet(key_data["key"].encode())

        return manager


class SecretMasker:
    """Utility for masking secrets in logs and UI"""

    @staticmethod
    def mask_secret(secret: str, visible_chars: int = 4, mask_char: str = "*") -> str:
        """
        Mask a secret, showing only the first few characters.

        Examples:
            "my_secret_password" -> "my_s************"
            "abc" -> "***"
        """
        if not secret:
            return ""

        if len(secret) <= visible_chars:
            return mask_char * len(secret)

        visible_part = secret[:visible_chars]
        masked_part = mask_char * (len(secret) - visible_chars)
        return visible_part + masked_part

    @staticmethod
    def mask_for_backup_display(secret: str) -> str:
        """Mask secret for backup display (show first and last 2 chars)"""
        if not secret or len(secret) < 8:
            return "****"

        return f"{secret[:2]}...{secret[-2:]}"

    @staticmethod
    def hash_secret_for_comparison(secret: str) -> str:
        """
        Create a hash of the secret for comparison purposes.
        Useful for verifying a secret matches without exposing it.
        """
        return hashlib.sha256(secret.encode()).hexdigest()[:16]

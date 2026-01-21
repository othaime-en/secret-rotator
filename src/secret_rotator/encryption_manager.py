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

    def rotate_master_key(self, providers: Dict[str, Any] = None) -> bool:
        """
        Rotate the master encryption key with two-phase commit for data safety.
        
        This implementation uses a two-phase commit approach:
        1. Phase 1: Validate and prepare - read all secrets, re-encrypt to memory
        2. Phase 2: Verify - ensure all re-encrypted secrets can be decrypted
        3. Phase 3: Commit - atomically write all changes to disk
        4. Phase 4: Update in-memory state
        
        If any phase fails, all changes are rolled back automatically.
        
        Args:
            providers: Dictionary of provider instances that need re-encryption
                    (passed from RotationEngine)
        
        Returns:
            True if rotation succeeded, False if failed (with automatic rollback)
        """
        if not self.cipher:
            raise ValueError("No master key to rotate")

        logger.info("=" * 70)
        logger.info("Starting master key rotation with two-phase commit")
        logger.info("=" * 70)

        new_key = Fernet.generate_key()
        new_cipher = Fernet(new_key)
        old_cipher = self.cipher  # Keep reference to old cipher
        
        new_metadata = {
            "version": self.key_metadata.get("version", 0) + 1,
            "created_at": datetime.now().isoformat(),
            "algorithm": "Fernet",
            "key_id": hashlib.sha256(new_key).hexdigest()[:16],
            "rotated_from": self.key_metadata.get("key_id"),
            "rotated_at": datetime.now().isoformat(),
        }

        # Track all backup files for cleanup/rollback
        backup_files = []

        try:
            # PHASE 0: Create backups BEFORE any changes
            logger.info("Phase 0: Creating safety backups...")
            
            # Backup master key file
            key_backup_path = self.key_file.with_suffix(".key.rotation_backup")
            if self.key_file.exists():
                import shutil
                shutil.copy2(self.key_file, key_backup_path)
                backup_files.append(key_backup_path)
                logger.info(f"✓ Backed up master key to {key_backup_path}")

            # PHASE 1: Prepare - Re-encrypt all secrets to memory
            logger.info("Phase 1: Re-encrypting all secrets (in memory)...")
            
            if not providers:
                logger.warning("No providers provided - only rotating key, no secrets to re-encrypt")
                re_encrypted_data = {}
            else:
                re_encrypted_data = {}
                
                for provider_name, provider in providers.items():
                    if not hasattr(provider, "encryption_manager"):
                        logger.debug(f"Provider {provider_name} has no encryption - skipping")
                        continue
                    
                    if not hasattr(provider, "file_path"):
                        logger.warning(f"Provider {provider_name} is not file-based - skipping")
                        continue
                    
                    logger.info(f"Processing provider: {provider_name}")
                    
                    # Backup provider's secrets file
                    provider_backup = provider.file_path.with_suffix(".json.rotation_backup")
                    if provider.file_path.exists():
                        import shutil
                        shutil.copy2(provider.file_path, provider_backup)
                        backup_files.append(provider_backup)
                        logger.info(f"  ✓ Backed up secrets file to {provider_backup}")
                    
                    # Read encrypted secrets from file
                    import json
                    try:
                        with open(provider.file_path, "r") as f:
                            encrypted_secrets = json.load(f)
                    except (FileNotFoundError, json.JSONDecodeError) as e:
                        logger.error(f"  ✗ Failed to read secrets file: {e}")
                        raise
                    
                    logger.info(f"  Found {len(encrypted_secrets)} secrets to re-encrypt")
                    
                    provider_re_encrypted = {}
                    
                    for secret_id, encrypted_value in encrypted_secrets.items():
                        try:
                            # Decrypt with OLD cipher directly
                            # Handle both plain base64 and JSON-wrapped formats
                            try:
                                # Try JSON format first (with metadata)
                                package = json.loads(encrypted_value)
                                if "ciphertext" in package:
                                    ciphertext_b64 = package["ciphertext"]
                                else:
                                    ciphertext_b64 = encrypted_value
                            except (json.JSONDecodeError, TypeError):
                                # Plain base64 format
                                ciphertext_b64 = encrypted_value
                            
                            # Decode base64 and decrypt
                            encrypted_bytes = base64.b64decode(ciphertext_b64.encode("utf-8"))
                            decrypted_bytes = old_cipher.decrypt(encrypted_bytes)
                            decrypted_value = decrypted_bytes.decode("utf-8")
                            
                            # Encrypt with NEW cipher
                            new_encrypted_bytes = new_cipher.encrypt(decrypted_value.encode("utf-8"))
                            new_ciphertext_b64 = base64.b64encode(new_encrypted_bytes).decode("utf-8")
                            
                            provider_re_encrypted[secret_id] = new_ciphertext_b64
                            
                            logger.debug(f"  ✓ Re-encrypted secret: {secret_id}")
                            
                        except Exception as e:
                            logger.error(f"  ✗ Failed to re-encrypt secret {secret_id}: {e}")
                            raise ValueError(f"Re-encryption failed for {secret_id}: {e}")
                    
                    re_encrypted_data[provider_name] = {
                        "secrets": provider_re_encrypted,
                        "file_path": provider.file_path
                    }
                    
                    logger.info(f"  ✓ Re-encrypted {len(provider_re_encrypted)} secrets for {provider_name}")

            # PHASE 2: Verify - Test decryption with new cipher
            logger.info("Phase 2: Verifying re-encrypted secrets...")
            
            for provider_name, data in re_encrypted_data.items():
                logger.info(f"Verifying provider: {provider_name}")
                
                for secret_id, encrypted_value in data["secrets"].items():
                    try:
                        # Decode and decrypt with NEW cipher
                        encrypted_bytes = base64.b64decode(encrypted_value.encode("utf-8"))
                        decrypted_bytes = new_cipher.decrypt(encrypted_bytes)
                        decrypted_value = decrypted_bytes.decode("utf-8")
                        
                        # Basic sanity check
                        if not decrypted_value:
                            raise ValueError(f"Decrypted value is empty for {secret_id}")
                        
                        logger.debug(f"  ✓ Verified secret: {secret_id}")
                        
                    except Exception as e:
                        logger.error(f"  ✗ Verification failed for {secret_id}: {e}")
                        raise ValueError(f"Verification failed for {secret_id}: {e}")
                
                logger.info(f"  ✓ All secrets verified for {provider_name}")

            # PHASE 3: Commit - Atomically write all changes
            logger.info("Phase 3: Committing changes to disk...")
            
            # Write re-encrypted secrets to provider files
            for provider_name, data in re_encrypted_data.items():
                try:
                    import json
                    with open(data["file_path"], "w") as f:
                        json.dump(data["secrets"], f, indent=2)
                    logger.info(f"  ✓ Updated secrets file for {provider_name}")
                except Exception as e:
                    logger.error(f"  ✗ Failed to write secrets for {provider_name}: {e}")
                    raise
            
            # Write new master key file
            key_data = {
                "key": new_key.decode("utf-8"),
                "metadata": new_metadata
            }
            
            try:
                with open(self.key_file, "w") as f:
                    json.dump(key_data, f, indent=2)
                os.chmod(self.key_file, 0o600)
                logger.info(f"  ✓ Updated master key file")
            except Exception as e:
                logger.error(f"  ✗ Failed to write new master key: {e}")
                raise

            # PHASE 4: Update in-memory state
            logger.info("Phase 4: Updating in-memory state...")
            
            # Update this encryption manager's state
            self.cipher = new_cipher
            self.key_metadata = new_metadata
            logger.info("  ✓ Updated encryption manager cipher")
            
            # Update provider encryption managers
            if providers:
                for provider_name, provider in providers.items():
                    if hasattr(provider, "encryption_manager"):
                        provider.encryption_manager.cipher = new_cipher
                        provider.encryption_manager.key_metadata = new_metadata
                        logger.info(f"  ✓ Updated cipher for provider: {provider_name}")

            # SUCCESS: Clean up backup files
            logger.info("Cleaning up backup files...")
            for backup_file in backup_files:
                try:
                    if backup_file.exists():
                        backup_file.unlink()
                        logger.debug(f"  Removed backup: {backup_file}")
                except Exception as e:
                    logger.warning(f"  Could not remove backup {backup_file}: {e}")

            logger.info("=" * 70)
            logger.info("✓ MASTER KEY ROTATION COMPLETED SUCCESSFULLY")
            logger.info(f"  New key ID: {new_metadata['key_id']}")
            logger.info(f"  Rotated from: {new_metadata.get('rotated_from', 'N/A')}")
            logger.info(f"  Providers updated: {len(re_encrypted_data)}")
            logger.info("=" * 70)
            
            return True

        except Exception as e:
            # FAILURE: Rollback all changes
            logger.error("=" * 70)
            logger.error(f"✗ MASTER KEY ROTATION FAILED: {e}")
            logger.error("=" * 70)
            logger.info("Rolling back all changes...")
            
            rollback_success = True
            
            # Restore master key from backup
            if key_backup_path.exists():
                try:
                    import shutil
                    shutil.copy2(key_backup_path, self.key_file)
                    logger.info("  ✓ Restored master key from backup")
                except Exception as rollback_error:
                    logger.error(f"  ✗ Failed to restore master key: {rollback_error}")
                    rollback_success = False
            
            # Restore provider secrets from backups
            for backup_file in backup_files:
                if backup_file.exists() and backup_file.suffix == ".rotation_backup":
                    try:
                        original_file = backup_file.with_suffix("")
                        import shutil
                        shutil.copy2(backup_file, original_file)
                        logger.info(f"  ✓ Restored {original_file.name}")
                    except Exception as rollback_error:
                        logger.error(f"  ✗ Failed to restore {backup_file}: {rollback_error}")
                        rollback_success = False
            
            # Reload old key and cipher
            try:
                key = self._load_existing_key()
                self.cipher = Fernet(key)
                logger.info("  ✓ Reloaded old cipher")
            except Exception as reload_error:
                logger.critical(f"  ✗ CRITICAL: Failed to reload old cipher: {reload_error}")
                rollback_success = False
            
            if rollback_success:
                logger.info("✓ Rollback completed successfully - system restored to previous state")
            else:
                logger.critical("✗ ROLLBACK FAILED - Manual intervention required!")
                logger.critical(f"  Backup files are preserved in: {self.key_file.parent}")
                logger.critical("  Contact support immediately!")
            
            logger.info("=" * 70)
            
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

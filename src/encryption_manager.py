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
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from utils.logger import logger
from datetime import datetime, timedelta


class EncryptionManager:
    """Handle encryption/decryption of secrets using a master key"""
    
    def __init__(self, key_file: str = "config/.master.key"):
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
            with open(self.key_file, 'r') as f:
                key_data = json.load(f)
            
            # Extract key and metadata
            key = base64.b64decode(key_data["key"].encode())
            self.key_metadata = key_data.get("metadata", {})
            
            # Verify key integrity
            expected_key_id = self.key_metadata.get("key_id")
            if expected_key_id:
                actual_key_id = hashlib.sha256(key).hexdigest()[:16]
                if expected_key_id != actual_key_id:
                    raise ValueError("Master key integrity check failed")
            
            return key
            
        except json.JSONDecodeError:
            # Handle legacy key files (raw bytes without metadata)
            logger.warning("Loading legacy key file without metadata")
            with open(self.key_file, 'rb') as f:
                key = f.read()
            
            # Create metadata for legacy key
            self.key_metadata = {
                "version": 0,
                "algorithm": "Fernet",
                "key_id": hashlib.sha256(key).hexdigest()[:16],
                "legacy": True
            }
            
            return key
    
    def _generate_and_save_key(self) -> bytes:
        """Generate a new encryption key and save it securely with metadata"""
        # Generate cryptographically secure random key
        key = Fernet.generate_key()
        
        # Create metadata
        self.key_metadata = {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "algorithm": "Fernet",
            "key_id": hashlib.sha256(key).hexdigest()[:16]
        }
        
        # Package key with metadata
        key_data = {
            "key": base64.b64encode(key).decode('utf-8'),
            "metadata": self.key_metadata
        }
        
        # Create config directory if it doesn't exist
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Save key with metadata as JSON
        with open(self.key_file, 'w') as f:
            json.dump(key_data, f, indent=2)
        
        # Set file permissions to 0600 (owner read/write only)
        os.chmod(self.key_file, 0o600)
        
        logger.warning(
            f"Master key generated at {self.key_file}. "
            "BACKUP THIS FILE SECURELY - it cannot be recovered if lost!"
        )
        
        return key
    
    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext and return base64-encoded ciphertext"""
        if not plaintext:
            return ""
        
        try:
            encrypted_bytes = self.cipher.encrypt(plaintext.encode('utf-8'))
            return base64.b64encode(encrypted_bytes).decode('utf-8')
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise
    
    def decrypt(self, ciphertext: str) -> str:
        """Decrypt base64-encoded ciphertext and return plaintext"""
        if not ciphertext:
            return ""
        
        try:
            encrypted_bytes = base64.b64decode(ciphertext.encode('utf-8'))
            decrypted_bytes = self.cipher.decrypt(encrypted_bytes)
            return decrypted_bytes.decode('utf-8')
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise
    
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
        }
        
        # Calculate age if creation date available
        if self.key_metadata.get("created_at"):
            try:
                created_at = datetime.fromisoformat(self.key_metadata["created_at"])
                age = datetime.now() - created_at
                info["age_days"] = age.days
            except:
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
                logger.info(f"Key is {age.days} days old (max: {max_age_days}), rotation recommended")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking key age: {e}")
            return True  # Err on the side of caution
     
        
    def rotate_master_key(self, new_key: Optional[bytes] = None):
        """
        Rotate the master encryption key.
        This requires re-encrypting all secrets with the new key.
        """
        if new_key is None:
            new_key = Fernet.generate_key()
        
        old_cipher = self.cipher
        new_cipher = Fernet(new_key)
        
        # This method should be called by the rotation engine
        # which will handle re-encrypting all secrets
        self.cipher = new_cipher
        
        # Save new key
        with open(self.key_file, 'wb') as f:
            f.write(new_key)
        
        os.chmod(self.key_file, 0o600)
        
        logger.info("Master encryption key rotated successfully")
        return old_cipher
    
    @staticmethod
    def derive_key_from_passphrase(passphrase: str, salt: Optional[bytes] = None) -> tuple[bytes, bytes]:
        """
        Derive an encryption key from a passphrase.
        Useful for environments where you can't store a key file.
        
        Returns: (derived_key, salt)
        """
        if salt is None:
            salt = os.urandom(16)
        
        kdf = PBKDF2(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
        return key, salt


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

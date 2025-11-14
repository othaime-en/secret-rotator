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
from pathlib import Path
from typing import Optional
from utils.logger import logger


class EncryptionManager:
    """Handle encryption/decryption of secrets using a master key"""
    
    def __init__(self, key_file: str = "config/.master.key"):
        self.key_file = Path(key_file)
        self.cipher = None
        self._initialize_encryption()
    
    def _initialize_encryption(self):
        """Initialize encryption cipher with master key"""
        if self.key_file.exists():
            with open(self.key_file, 'rb') as f:
                key = f.read()
            logger.info("Loaded existing master encryption key")
        else:
            key = self._generate_and_save_key()
            logger.info("Generated new master encryption key")
        
        self.cipher = Fernet(key)
    
    def _generate_and_save_key(self) -> bytes:
        """Generate a new encryption key and save it securely"""
        key = Fernet.generate_key()
        
        # Create config directory if it doesn't exist
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Save key with restricted permissions (owner read/write only)
        with open(self.key_file, 'wb') as f:
            f.write(key)
        
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
        import hashlib
        return hashlib.sha256(secret.encode()).hexdigest()[:16]


"""
Encryption manager for securing secrets at rest and in backups.
Uses Fernet (symmetric encryption) from cryptography library.
"""
from cryptography.fernet import Fernet
import os
from pathlib import Path
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
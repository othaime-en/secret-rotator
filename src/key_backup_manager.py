"""
Master_Key Backup and Recovery System.
This module provides functionality to back up and recover master encryption keys.
"""
import json
import os
import hashlib
import secrets
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import base64
from utils.logger import logger


class MasterKeyBackupManager:
    """
    Manage backup and recovery of master encryption keys.
    Implements multiple backup strategies for disaster recovery.
    """
    
    def __init__(
        self,
        master_key_file: str = "config/.master.key",
        backup_dir: str = "config/key_backups"
    ):
        self.master_key_file = Path(master_key_file)
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Set restrictive permissions on backup directory
        os.chmod(self.backup_dir, 0o700)
    
    def create_encrypted_key_backup(
        self,
        passphrase: str,
        backup_name: Optional[str] = None
    ) -> str:
        """
        Create an encrypted backup of the master key using a passphrase.
        
        This allows you to store the backup in less secure locations
        since it's protected by the passphrase.
        
        Args:
            passphrase: Strong passphrase to encrypt the backup
            backup_name: Optional name for the backup file
            
        Returns:
            Path to the encrypted backup file
        """
        if not self.master_key_file.exists():
            raise FileNotFoundError(f"Master key not found: {self.master_key_file}")
        
        # Read the master key
        with open(self.master_key_file, 'r') as f:
            key_data = json.load(f)
        
        # Generate salt for key derivation
        salt = secrets.token_bytes(32)
        
        # Derive encryption key from passphrase
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=600000,  # OWASP 2023 recommendation
            backend=default_backend()
        )
        derived_key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
        
        # Encrypt the master key data
        cipher = Fernet(derived_key)
        key_json = json.dumps(key_data)
        encrypted_data = cipher.encrypt(key_json.encode())
        
        # Create backup package
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = backup_name or f"master_key_backup_{timestamp}"
        backup_file = self.backup_dir / f"{backup_name}.enc"
        
        backup_package = {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "salt": base64.b64encode(salt).decode(),
            "iterations": 600000,
            "encrypted_key_data": base64.b64encode(encrypted_data).decode(),
            "key_id": key_data.get("metadata", {}).get("key_id"),
            "checksum": self._calculate_checksum(encrypted_data)
        }
        
        # Write encrypted backup
        with open(backup_file, 'w') as f:
            json.dump(backup_package, f, indent=2)
        
        # Set restrictive permissions
        os.chmod(backup_file, 0o600)
        
        logger.info(f"Created encrypted key backup: {backup_file}")
        logger.warning(
            f"IMPORTANT: Store the passphrase securely. "
            f"Without it, this backup cannot be recovered!"
        )
        
        return str(backup_file)
    
    def restore_from_encrypted_backup(
        self,
        backup_file: str,
        passphrase: str,
        verify_only: bool = False
    ) -> bool:
        """
        Restore master key from encrypted backup.
        
        Args:
            backup_file: Path to encrypted backup file
            passphrase: Passphrase used to create the backup
            verify_only: If True, only verify backup can be decrypted, don't restore
            
        Returns:
            True if successful
        """
        backup_path = Path(backup_file)
        
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_file}")
        
        try:
            # Read backup package
            with open(backup_path, 'r') as f:
                backup_package = json.load(f)
            
            # Extract backup components
            salt = base64.b64decode(backup_package['salt'])
            iterations = backup_package['iterations']
            encrypted_data = base64.b64decode(backup_package['encrypted_key_data'])
            stored_checksum = backup_package.get('checksum')
            
            # Verify checksum
            if stored_checksum:
                calculated_checksum = self._calculate_checksum(encrypted_data)
                if calculated_checksum != stored_checksum:
                    raise ValueError("Backup checksum verification failed - file may be corrupted")
            
            # Derive decryption key from passphrase
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=iterations,
                backend=default_backend()
            )
            derived_key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
            
            # Decrypt the master key data
            cipher = Fernet(derived_key)
            decrypted_data = cipher.decrypt(encrypted_data)
            key_data = json.loads(decrypted_data.decode())
            
            logger.info("Successfully decrypted backup")
            
            if verify_only:
                logger.info("Verification successful - backup is valid")
                return True
            
            # Create backup of current key before restoring
            if self.master_key_file.exists():
                current_backup = self.master_key_file.with_suffix('.key.pre_restore')
                import shutil
                shutil.copy2(self.master_key_file, current_backup)
                logger.info(f"Backed up current key to: {current_backup}")
            
            # Restore the master key
            with open(self.master_key_file, 'w') as f:
                json.dump(key_data, f, indent=2)
            
            os.chmod(self.master_key_file, 0o600)
            
            logger.info(f"Successfully restored master key from backup")
            logger.warning(
                "IMPORTANT: You may need to restart the application for changes to take effect"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to restore from backup: {e}")
            raise
    
    def _calculate_checksum(self, data: bytes) -> str:
        """Calculate SHA-256 checksum"""
        return hashlib.sha256(data).hexdigest()
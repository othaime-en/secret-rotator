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
    
    def create_split_key_backup(
        self,
        num_shares: int = 5,
        threshold: int = 3
    ) -> List[str]:
        """
        Create a Shamir's Secret Sharing backup of the master key.
        
        Splits the key into N shares where any K shares can reconstruct it.
        This allows distributed storage with no single point of failure.
        
        Args:
            num_shares: Total number of shares to create
            threshold: Minimum number of shares needed to reconstruct
            
        Returns:
            List of share file paths
        """
        try:
            from secretsharing import PlaintextToHexSecretSharer
        except ImportError:
            logger.error(
                "secretsharing library not installed. "
                "Install with: pip install secretsharing"
            )
            raise
        
        if threshold > num_shares:
            raise ValueError("Threshold cannot exceed number of shares")
        
        if not self.master_key_file.exists():
            raise FileNotFoundError(f"Master key not found: {self.master_key_file}")
        
        # Read the master key
        with open(self.master_key_file, 'r') as f:
            key_data = json.load(f)
        
        # Convert to hex string for splitting
        key_json = json.dumps(key_data)
        key_hex = key_json.encode().hex()
        
        # Split the key
        shares = PlaintextToHexSecretSharer.split_secret(key_hex, threshold, num_shares)
        
        # Save each share
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        share_files = []
        
        for i, share in enumerate(shares, 1):
            share_file = self.backup_dir / f"master_key_share_{i}_of_{num_shares}_{timestamp}.share"
            
            share_package = {
                "version": 1,
                "share_number": i,
                "total_shares": num_shares,
                "threshold": threshold,
                "created_at": datetime.now().isoformat(),
                "share_data": share,
                "key_id": key_data.get("metadata", {}).get("key_id")
            }
            
            with open(share_file, 'w') as f:
                json.dump(share_package, f, indent=2)
            
            os.chmod(share_file, 0o600)
            share_files.append(str(share_file))
            
            logger.info(f"Created key share {i}/{num_shares}: {share_file}")
        
        logger.warning(
            f"IMPORTANT: Distribute these {num_shares} shares to different secure locations. "
            f"Any {threshold} shares can reconstruct the key."
        )
        
        return share_files
    
    def restore_from_split_key(
        self,
        share_files: List[str],
        verify_only: bool = False
    ) -> bool:
        """
        Restore master key from Shamir shares.
        
        Args:
            share_files: List of paths to share files
            verify_only: If True, only verify shares can reconstruct, don't restore
            
        Returns:
            True if successful
        """
        try:
            from secretsharing import PlaintextToHexSecretSharer
        except ImportError:
            logger.error("secretsharing library not installed")
            raise
        
        if not share_files:
            raise ValueError("No share files provided")
        
        # Read all shares
        shares = []
        threshold = None
        key_id = None
        
        for share_file in share_files:
            if not Path(share_file).exists():
                raise FileNotFoundError(f"Share file not found: {share_file}")
            
            with open(share_file, 'r') as f:
                share_package = json.load(f)
            
            shares.append(share_package['share_data'])
            
            if threshold is None:
                threshold = share_package['threshold']
            
            if key_id is None:
                key_id = share_package.get('key_id')
        
        if len(shares) < threshold:
            raise ValueError(
                f"Insufficient shares: need {threshold}, have {len(shares)}"
            )
        
        logger.info(f"Reconstructing key from {len(shares)} shares (threshold: {threshold})")
        
        # Reconstruct the key
        reconstructed_hex = PlaintextToHexSecretSharer.recover_secret(shares)
        reconstructed_json = bytes.fromhex(reconstructed_hex).decode()
        key_data = json.loads(reconstructed_json)
        
        # Verify key ID matches if available
        if key_id and key_data.get("metadata", {}).get("key_id") != key_id:
            logger.warning("Reconstructed key ID doesn't match expected key ID")
        
        logger.info("Successfully reconstructed master key from shares")
        
        if verify_only:
            logger.info("Verification successful - shares can reconstruct key")
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
        
        logger.info("Successfully restored master key from shares")
        
        return True
    
    def _calculate_checksum(self, data: bytes) -> str:
        """Calculate SHA-256 checksum"""
        return hashlib.sha256(data).hexdigest()
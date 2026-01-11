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
from typing import Dict, Any, List, Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import base64
from secret_rotator.utils.logger import logger


class MasterKeyBackupManager:
    """
    Manage backup and recovery of master encryption keys.
    Implements multiple backup strategies for disaster recovery.
    """

    def __init__(
        self, master_key_file: str = "config/.master.key", backup_dir: str = "config/key_backups"
    ):
        self.master_key_file = Path(master_key_file)
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # Set restrictive permissions on backup directory
        os.chmod(self.backup_dir, 0o700)

    def create_encrypted_key_backup(
        self, passphrase: str, backup_name: Optional[str] = None
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
        with open(self.master_key_file, "r") as f:
            key_data = json.load(f)

        # Generate salt for key derivation
        salt = secrets.token_bytes(32)

        # Derive encryption key from passphrase
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=600000,  # OWASP 2023 recommendation
            backend=default_backend(),
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
            "checksum": self._calculate_checksum(encrypted_data),
        }

        # Write encrypted backup
        with open(backup_file, "w") as f:
            json.dump(backup_package, f, indent=2)

        # Set restrictive permissions
        os.chmod(backup_file, 0o600)

        logger.info(f"Created encrypted key backup: {backup_file}")
        logger.warning(
            "IMPORTANT: Store the passphrase securely. "
            "Without it, this backup cannot be recovered!"
        )

        return str(backup_file)

    def restore_from_encrypted_backup(
        self, backup_file: str, passphrase: str, verify_only: bool = False
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
            with open(backup_path, "r") as f:
                backup_package = json.load(f)

            # Extract backup components
            salt = base64.b64decode(backup_package["salt"])
            iterations = backup_package["iterations"]
            encrypted_data = base64.b64decode(backup_package["encrypted_key_data"])
            stored_checksum = backup_package.get("checksum")

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
                backend=default_backend(),
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
                current_backup = self.master_key_file.with_suffix(".key.pre_restore")
                import shutil

                shutil.copy2(self.master_key_file, current_backup)
                logger.info(f"Backed up current key to: {current_backup}")

            # Restore the master key
            with open(self.master_key_file, "w") as f:
                json.dump(key_data, f, indent=2)

            os.chmod(self.master_key_file, 0o600)

            logger.info("Successfully restored master key from backup")
            logger.warning(
                "IMPORTANT: You may need to restart the application for changes to take effect"
            )

            return True

        except Exception as e:
            logger.error(f"Failed to restore from backup: {e}")
            raise

    def create_split_key_backup(self, num_shares: int = 5, threshold: int = 3) -> List[str]:
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
            from pyshamir import split
        except ImportError:
            logger.error("pyshamir library not installed. " "Install with: pip install pyshamir")
            raise

        if threshold > num_shares:
            raise ValueError("Threshold cannot exceed number of shares")

        if not self.master_key_file.exists():
            raise FileNotFoundError(f"Master key not found: {self.master_key_file}")

        # Read the master key
        with open(self.master_key_file, "r") as f:
            key_data = json.load(f)

        # Convert to bytes for splitting
        key_json = json.dumps(key_data)
        key_bytes = key_json.encode("utf-8")

        # Split the key and save each share
        shares = split(key_bytes, num_shares, threshold)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        share_files = []

        for i, share in enumerate(shares, 1):
            share_file = self.backup_dir / f"master_key_share_{i}_of_{num_shares}_{timestamp}.share"

            # Convert share bytes to base64 for JSON storage
            import base64

            share_base64 = base64.b64encode(share).decode("utf-8")

            share_package = {
                "version": 1,
                "share_number": i,
                "total_shares": num_shares,
                "threshold": threshold,
                "created_at": datetime.now().isoformat(),
                "share_data": share_base64,
                "key_id": key_data.get("metadata", {}).get("key_id"),
            }

            with open(share_file, "w") as f:
                json.dump(share_package, f, indent=2)

            os.chmod(share_file, 0o600)
            share_files.append(str(share_file))

            logger.info(f"Created key share {i}/{num_shares}: {share_file}")

        logger.warning(
            f"IMPORTANT: Distribute these {num_shares} shares to different secure locations. "
            f"Any {threshold} shares can reconstruct the key."
        )

        return share_files

    def restore_from_split_key(self, share_files: List[str], verify_only: bool = False) -> bool:
        """
        Restore master key from Shamir shares.

        Args:
            share_files: List of paths to share files
            verify_only: If True, only verify shares can reconstruct, don't restore

        Returns:
            True if successful
        """
        try:
            from pyshamir import combine
        except ImportError:
            logger.error("pyshamir library not installed")
            raise

        if not share_files:
            raise ValueError("No share files provided")

        # Read all shares
        shares_bytes = []
        threshold = None
        key_id = None

        for share_file in share_files:
            if not Path(share_file).exists():
                raise FileNotFoundError(f"Share file not found: {share_file}")

            with open(share_file, "r") as f:
                share_package = json.load(f)

            # Convert base64 back to bytes
            import base64

            share_bytes = base64.b64decode(share_package["share_data"])
            shares_bytes.append(share_bytes)

            if threshold is None:
                threshold = share_package["threshold"]

            if key_id is None:
                key_id = share_package.get("key_id")

        if len(shares_bytes) < threshold:
            raise ValueError(f"Insufficient shares: need {threshold}, have {len(shares_bytes)}")

        logger.info(f"Reconstructing key from {len(shares_bytes)} shares (threshold: {threshold})")

        # Reconstruct the key
        reconstructed_bytes = combine(shares_bytes)
        reconstructed_json = reconstructed_bytes.decode("utf-8")
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
            current_backup = self.master_key_file.with_suffix(".key.pre_restore")
            import shutil

            shutil.copy2(self.master_key_file, current_backup)
            logger.info(f"Backed up current key to: {current_backup}")

        # Restore the master key
        with open(self.master_key_file, "w") as f:
            json.dump(key_data, f, indent=2)

        os.chmod(self.master_key_file, 0o600)

        logger.info("Successfully restored master key from shares")

        return True

    def create_plaintext_backup(self, backup_name: Optional[str] = None) -> str:
        """
        Create an unencrypted backup of the master key.

        WARNING: Use only for immediate manual secure storage.
        The backup file must be stored in a physically secure location.

        Args:
            backup_name: Optional name for the backup file

        Returns:
            Path to the backup file
        """
        if not self.master_key_file.exists():
            raise FileNotFoundError(f"Master key not found: {self.master_key_file}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = backup_name or f"master_key_backup_{timestamp}"
        backup_file = self.backup_dir / f"{backup_name}.key"

        # Copy the key file
        import shutil

        shutil.copy2(self.master_key_file, backup_file)

        # Set restrictive permissions
        os.chmod(backup_file, 0o600)

        logger.warning(
            f"Created UNENCRYPTED key backup: {backup_file}\n"
            f"WARNING: This file must be stored in a physically secure location!\n"
            f"Consider using encrypted or split-key backups instead."
        )

        return str(backup_file)

    def list_backups(self) -> List[Dict[str, Any]]:
        """List all available key backups"""
        backups = []

        # Find encrypted backups
        for backup_file in self.backup_dir.glob("*.enc"):
            try:
                with open(backup_file, "r") as f:
                    backup_data = json.load(f)

                backups.append(
                    {
                        "type": "encrypted",
                        "file": str(backup_file),
                        "created_at": backup_data.get("created_at"),
                        "key_id": backup_data.get("key_id"),
                        "status": "available",
                    }
                )
            except Exception as e:
                logger.warning(f"Error reading backup {backup_file}: {e}")

        # Find share backups (group by timestamp)
        share_groups = {}
        for share_file in self.backup_dir.glob("*.share"):
            try:
                with open(share_file, "r") as f:
                    share_data = json.load(f)

                timestamp = share_data.get("created_at")
                if timestamp not in share_groups:
                    share_groups[timestamp] = {
                        "type": "split_key",
                        "created_at": timestamp,
                        "threshold": share_data.get("threshold"),
                        "total_shares": share_data.get("total_shares"),
                        "key_id": share_data.get("key_id"),
                        "shares": [],
                    }

                share_groups[timestamp]["shares"].append(str(share_file))
            except Exception as e:
                logger.warning(f"Error reading share {share_file}: {e}")

        # Add share groups to backups
        for share_group in share_groups.values():
            share_group["available_shares"] = len(share_group["shares"])
            share_group["status"] = (
                "complete"
                if share_group["available_shares"] >= share_group["threshold"]
                else "incomplete"
            )
            backups.append(share_group)

        # Find plaintext backups
        for backup_file in self.backup_dir.glob("*.key"):
            if backup_file.name != self.master_key_file.name:
                stat = backup_file.stat()
                backups.append(
                    {
                        "type": "plaintext",
                        "file": str(backup_file),
                        "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "status": "available",
                        "warning": "unencrypted",
                    }
                )

        return sorted(backups, key=lambda x: x.get("created_at", ""), reverse=True)

    def verify_backup(self, backup_file: str, passphrase: Optional[str] = None) -> bool:
        """
        Verify a backup can be successfully restored.

        Args:
            backup_file: Path to backup file
            passphrase: Required for encrypted backups

        Returns:
            True if backup is valid
        """
        backup_path = Path(backup_file)

        if not backup_path.exists():
            logger.error(f"Backup file not found: {backup_file}")
            return False

        try:
            if backup_path.suffix == ".enc":
                if not passphrase:
                    logger.error("Passphrase required for encrypted backup")
                    return False
                return self.restore_from_encrypted_backup(backup_file, passphrase, verify_only=True)

            elif backup_path.suffix == ".share":
                logger.warning("Cannot verify single share - need threshold shares to verify")
                return False

            elif backup_path.suffix == ".key":
                # Verify it's valid JSON
                with open(backup_path, "r") as f:
                    json.load(f)
                logger.info("Plaintext backup verified")
                return True

            else:
                logger.error(f"Unknown backup type: {backup_path.suffix}")
                return False

        except Exception as e:
            logger.error(f"Backup verification failed: {e}")
            return False

    def _calculate_checksum(self, data: bytes) -> str:
        """Calculate SHA-256 checksum"""
        return hashlib.sha256(data).hexdigest()

    def export_backup_instructions(self, output_file: str = "KEY_BACKUP_INSTRUCTIONS.txt"):
        """Generate human-readable backup instructions"""
        instructions = f"""
=============================================================================
MASTER KEY BACKUP AND RECOVERY INSTRUCTIONS
Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
=============================================================================

IMPORTANT: Read and follow these instructions carefully. Losing access to
your master encryption key means ALL encrypted secrets become unrecoverable.

BACKUP LOCATIONS
================
Master Key File: {self.master_key_file}
Backup Directory: {self.backup_dir}

AVAILABLE BACKUP TYPES
=======================

1. ENCRYPTED BACKUP (Recommended for remote storage)
   - Protected by passphrase
   - Can be stored in cloud storage
   - Requires strong passphrase (20+ characters)

   Create: python -c "from secret_rotator.key_backup_manager import MasterKeyBackupManager; \\
                      mgr = MasterKeyBackupManager(); \\
                      mgr.create_encrypted_key_backup('YOUR_STRONG_PASSPHRASE')"

   Restore: python -c "from secret_rotator.key_backup_manager import MasterKeyBackupManager; \\
                       mgr = MasterKeyBackupManager(); \\
                       mgr.restore_from_encrypted_backup('backup_file.enc', 'YOUR_PASSPHRASE')"

2. SPLIT KEY BACKUP (Recommended for distributed storage)
   - Key split into multiple shares using Shamir's Secret Sharing
   - Any K of N shares can reconstruct the key
   - No single person/location has complete key

   Create: python -c "from secret_rotator.key_backup_manager import MasterKeyBackupManager; \\
                      mgr = MasterKeyBackupManager(); \\
                      mgr.create_split_key_backup(num_shares=5, threshold=3)"

   Restore: python -c "from secret_rotator.key_backup_manager import MasterKeyBackupManager; \\
                       mgr = MasterKeyBackupManager(); \\
                       mgr.restore_from_split_key(['share1.share', 'share2.share', 'share3.share'])"

3. PLAINTEXT BACKUP (Use only for immediate physical storage)
   - Unencrypted copy of master key
   - MUST be stored in physically secure location (safe, vault)
   - Should be used only as last resort

   Create: python -c "from secret_rotator.key_backup_manager import MasterKeyBackupManager; \\
                      mgr = MasterKeyBackupManager(); \\
                      mgr.create_plaintext_backup()"

BACKUP BEST PRACTICES
======================
1. Create backups IMMEDIATELY after key generation
2. Test backup restoration in non-production environment
3. Store backups in multiple secure locations:
   - Physical safe/vault (plaintext backup)
   - Password manager (encrypted backup passphrase)
   - Distributed across trusted parties (split key shares)
4. Document backup locations and recovery procedures
5. Regularly verify backups are accessible
6. Update backups after key rotation

RECOVERY PROCEDURES
===================
If master key is lost:
1. Locate your backup files
2. Verify backup integrity before restoration
3. Stop the secret rotation application
4. Restore master key from backup
5. Restart the application
6. Verify application can decrypt existing secrets

EMERGENCY CONTACTS
==================
System Administrator: [YOUR NAME/EMAIL]
Backup Custodians: [LIST PEOPLE WHO HAVE ACCESS TO BACKUPS]
"""

        output_path = Path(output_file)
        with open(output_path, "w") as f:
            f.write(instructions)

        logger.info(f"Backup instructions written to: {output_path}")
        return str(output_path)

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from utils.logger import logger
from encryption_manager import EncryptionManager, SecretMasker

class BackupManager:
    """Handle backup and recovery of secrets with encryption support"""
    
    def __init__(self, backup_dir: str = "data/backup", encrypt_backups: bool = True):
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.encrypt_backups = encrypt_backups
        
        # Initialize encryption manager if encryption is enabled
        self.encryption_manager = None
        if self.encrypt_backups:
            self.encryption_manager = EncryptionManager()
            logger.info("Backup encryption enabled")
    
    def create_backup(self, secret_id: str, old_value: str, new_value: str) -> str:
        """Create an encrypted backup of the old secret value"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_filename = f"{secret_id}_{timestamp}.json"
        backup_path = self.backup_dir / backup_filename
        
        # Prepare backup data
        backup_data = {
            "secret_id": secret_id,
            "timestamp": timestamp,
            "old_value": old_value,
            "new_value": new_value,
            "backup_created": datetime.now().isoformat(),
            "encrypted": self.encrypt_backups
        }
        
        # Encrypt sensitive values if encryption is enabled
        if self.encrypt_backups and self.encryption_manager:
            try:
                backup_data["old_value"] = self.encryption_manager.encrypt(old_value)
                backup_data["new_value"] = self.encryption_manager.encrypt(new_value)
                logger.debug(f"Encrypted backup data for {secret_id}")
            except Exception as e:
                logger.error(f"Failed to encrypt backup for {secret_id}: {e}")
                raise
        
        try:
            with open(backup_path, 'w') as f:
                json.dump(backup_data, f, indent=2)
            
            logger.info(f"Created backup for {secret_id}: {backup_path}")
            return str(backup_path)
            
        except Exception as e:
            logger.error(f"Failed to create backup for {secret_id}: {e}")
            raise
    
    def restore_backup(self, backup_file: str, decrypt: bool = True) -> Dict[str, Any]:
        """Load and optionally decrypt backup data for restoration"""
        backup_path = Path(backup_file)
        
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_file}")
        
        try:
            with open(backup_path, 'r') as f:
                backup_data = json.load(f)
            
            # Decrypt values if backup was encrypted and decryption is requested
            is_encrypted = backup_data.get('encrypted', False)
            if is_encrypted and decrypt and self.encryption_manager:
                try:
                    backup_data['old_value'] = self.encryption_manager.decrypt(
                        backup_data['old_value']
                    )
                    backup_data['new_value'] = self.encryption_manager.decrypt(
                        backup_data['new_value']
                    )
                    logger.debug(f"Decrypted backup data from {backup_file}")
                except Exception as e:
                    logger.error(f"Failed to decrypt backup {backup_file}: {e}")
                    raise
            
            logger.info(f"Loaded backup data from {backup_file}")
            return backup_data
            
        except Exception as e:
            logger.error(f"Failed to restore backup {backup_file}: {e}")
            raise
    
    def list_backups(self, secret_id: Optional[str] = None, mask_values: bool = True) -> list:
        """List available backups with masked secret values"""
        backups = []
        pattern = f"{secret_id}_*.json" if secret_id else "*.json"
        
        for backup_file in self.backup_dir.glob(pattern):
            try:
                with open(backup_file, 'r') as f:
                    backup_data = json.load(f)
                    backup_data['backup_file'] = str(backup_file)

                    # Mask sensitive values in the listing
                    if mask_values:
                        is_encrypted = backup_data.get('encrypted', False)
                        
                        if is_encrypted and self.encryption_manager:
                            # For encrypted backups, decrypt then mask
                            try:
                                old_decrypted = self.encryption_manager.decrypt(
                                    backup_data['old_value']
                                )
                                new_decrypted = self.encryption_manager.decrypt(
                                    backup_data['new_value']
                                )
                                backup_data['old_value_masked'] = SecretMasker.mask_for_backup_display(
                                    old_decrypted
                                )
                                backup_data['new_value_masked'] = SecretMasker.mask_for_backup_display(
                                    new_decrypted
                                )
                            except Exception as e:
                                logger.warning(f"Could not decrypt backup for masking: {e}")
                                backup_data['old_value_masked'] = "****"
                                backup_data['new_value_masked'] = "****"
                        else:
                            # For plaintext backups, just mask
                            backup_data['old_value_masked'] = SecretMasker.mask_for_backup_display(
                                backup_data['old_value']
                            )
                            backup_data['new_value_masked'] = SecretMasker.mask_for_backup_display(
                                backup_data['new_value']
                            )
                        
                        # Remove actual values from listing for security
                        backup_data.pop('old_value', None)
                        backup_data.pop('new_value', None)
                                       
                    backups.append(backup_data)
                    
            except Exception as e:
                logger.warning(f"Failed to read backup file {backup_file}: {e}")
        
        # Sort by timestamp (newest first)
        backups.sort(key=lambda x: x['timestamp'], reverse=True)
        return backups
    
    def cleanup_old_backups(self, days_to_keep: int = 30):
        """Remove backup files older than specified days"""
        cutoff_timestamp = datetime.now().timestamp() - (days_to_keep * 24 * 60 * 60)
        removed_count = 0
        
        for backup_file in self.backup_dir.glob("*.json"):
            try:
                file_timestamp = backup_file.stat().st_mtime
                if file_timestamp < cutoff_timestamp:
                    backup_file.unlink()
                    removed_count += 1
                    logger.info(f"Removed old backup: {backup_file}")
                    
            except Exception as e:
                logger.warning(f"Failed to remove backup {backup_file}: {e}")
        
        logger.info(f"Cleanup complete: removed {removed_count} old backup files")
        return removed_count
    
    def verify_backup_integrity(self, backup_file: str) -> bool:
        """Verify that a backup can be successfully read and decrypted"""
        try:
            backup_data = self.restore_backup(backup_file, decrypt=True)
            
            # Check required fields
            required_fields = ['secret_id', 'old_value', 'new_value', 'timestamp']
            for field in required_fields:
                if field not in backup_data:
                    logger.error(f"Backup {backup_file} missing required field: {field}")
                    return False
            
            logger.info(f"Backup {backup_file} integrity verified")
            return True
            
        except Exception as e:
            logger.error(f"Backup integrity check failed for {backup_file}: {e}")
            return False
    
    def export_backup_metadata(self) -> Dict[str, Any]:
        """Export backup metadata for reporting (without secret values)"""
        all_backups = self.list_backups(mask_values=True)
        
        metadata = {
            "total_backups": len(all_backups),
            "encryption_enabled": self.encrypt_backups,
            "backup_directory": str(self.backup_dir),
            "secrets_with_backups": len(set(b['secret_id'] for b in all_backups)),
            "oldest_backup": all_backups[-1]['timestamp'] if all_backups else None,
            "newest_backup": all_backups[0]['timestamp'] if all_backups else None
        }
        
        return metadata


backups = BackupManager().export_backup_metadata()
print(backups)
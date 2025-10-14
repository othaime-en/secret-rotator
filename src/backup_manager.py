import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from utils.logger import logger

class BackupManager:
    """Handle backup and recovery of secrets"""
    
    def __init__(self, backup_dir: str = "data/backup"):
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def create_backup(self, secret_id: str, old_value: str, new_value: str) -> str:
        """ Create a backup of the old secret value """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_filename = f"{secret_id}_{timestamp}.json"
        backup_path = self.backup_dir / backup_filename
        
        backup_data = {
            "secret_id": secret_id,
            "timestamp": timestamp,
            "old_value": old_value,
            "new_value": new_value,
            "backup_created": datetime.now().isoformat()
        }
        
        try:
            with open(backup_path, 'w') as f:
                json.dump(backup_data, f, indent=2)
            
            logger.info(f"Created backup for {secret_id}: {backup_path}")
            return str(backup_path)
            
        except Exception as e:
            logger.error(f"Failed to create backup for {secret_id}: {e}")
            raise
    
    def restore_backup(self, backup_file: str) -> Dict[str, Any]:
        """ Load backup data for restoration """
        backup_path = Path(backup_file)
        
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_file}")
        
        try:
            with open(backup_path, 'r') as f:
                backup_data = json.load(f)
            
            logger.info(f"Loaded backup data from {backup_file}")
            return backup_data
            
        except Exception as e:
            logger.error(f"Failed to restore backup {backup_file}: {e}")
            raise
    

    def list_backups(self, secret_id: Optional[str] = None) -> list:
        """List available backup"""
        backups = []
        pattern = f"{secret_id}_*.json" if secret_id else "*.json"
        
        for backup_file in self.backup_dir.glob(pattern):
            try:
                with open(backup_file, 'r') as f:
                    backup_data = json.load(f)
                    backup_data['backup_file'] = str(backup_file)
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


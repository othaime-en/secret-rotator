import json
import os
from pathlib import Path
from typing import Dict, Any
from .base import SecretProvider
from utils.logger import logger
from utils.retry import retry_with_backoff

class FileSecretProvider(SecretProvider):
    """Simple file-based secret storage for testing"""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.file_path = Path(config.get('file_path', 'secrets.json'))
        self.ensure_file_exists()
    
    def ensure_file_exists(self):
        """Create secrets file if it doesn't exist"""
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, 'w') as f:
                json.dump({}, f)
    
    @retry_with_backoff(max_attempts=3, initial_delay=0.5, exceptions=(IOError, json.JSONDecodeError))
    def get_secret(self, secret_id: str) -> str:
        """Retrieve a secret from file"""
        try:
            with open(self.file_path, 'r') as f:
                secrets = json.load(f)
                return secrets.get(secret_id, "")
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.error(f"Error reading secrets file: {e}")
            return ""
    
    @retry_with_backoff(max_attempts=3, initial_delay=0.5, exceptions=(IOError,))
    def update_secret(self, secret_id: str, new_value: str) -> bool:
        """Update secret in file"""
        try:
            # Read current secrets
            with open(self.file_path, 'r') as f:
                secrets = json.load(f)
            
            # Update secret
            secrets[secret_id] = new_value
            
            # Write back to file
            with open(self.file_path, 'w') as f:
                json.dump(secrets, f, indent=2)
            
            logger.info(f"Successfully updated secret: {secret_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating secret {secret_id}: {e}")
            return False
    
    @retry_with_backoff(max_attempts=2, exceptions=(OSError,))
    def validate_connection(self) -> bool:
        """Test if file can be accessed"""
        try:
            return self.file_path.exists() and os.access(self.file_path, os.R_OK | os.W_OK)
        except Exception as e:
            logger.error(f"Connection validation failed: {e}")
            return False
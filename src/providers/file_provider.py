import json
import os
from pathlib import Path
from typing import Dict, Any
from .base import SecretProvider
from src.utils.logger import logger

class FileSecretProvider(SecretProvider):
    """Simple file-based secret storage for testing"""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.file_path = Path(config.get('file_path', 'secrets.json'))
        self.ensure_file_exists()
    
    def get_secret(self, secret_id: str) -> str:
        """Retrieve a secret from file"""
        with open(self.file_path, 'r') as f:
            secrets = json.load(f)
            return secrets.get(secret_id, "")
    
    def update_secret(self, secret_id: str, new_value: str) -> bool:
        """Update secret in file"""
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
        
        
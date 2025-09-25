from typing import Dict, List, Any
from src.providers.base import SecretProvider
from src.rotators.base import SecretRotator
from src.utils.logger import logger

class RotationEngine:
    """This is the main engine that orchestrates secret rotation"""
    
    def __init__(self):
        self.providers: Dict[str, SecretProvider] = {}
        self.rotators: Dict[str, SecretRotator] = {}
        self.rotation_jobs: List[Dict[str, Any]] = []
    
    def register_provider(self, provider: SecretProvider):
        self.providers[provider.name] = provider
        logger.info(f"Registered provider: {provider.name}")
    
    def register_rotator(self, rotator: SecretRotator):
        self.rotators[rotator.name] = rotator
        logger.info(f"Registered rotator: {rotator.name}")
    
    def add_rotation_job(self, job_config: Dict[str, Any]):
        required_fields = ['name', 'provider', 'rotator', 'secret_id']
        for field in required_fields:
            if field not in job_config:
                logger.error(f"Missing required field '{field}' in job config")
                return False
        
        self.rotation_jobs.append(job_config)
        logger.info(f"Added rotation job: {job_config['name']}")
        return True
    
    def rotate_secret(self, job_config: Dict[str, Any]) -> bool:
        """Rotate a single secret"""
        pass
    
    def rotate_all_secrets(self) -> Dict[str, bool]:
        """Rotate all configured secrets"""
        pass
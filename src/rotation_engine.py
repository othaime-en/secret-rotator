import time
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
        """Rotate a single secret based on job configuration"""
        job_name = job_config['name']
        provider_name = job_config['provider']
        rotator_name = job_config['rotator']
        secret_id = job_config['secret_id']
        
        logger.info(f"Starting rotation for job: {job_name}")
        
        # Get provider and rotator
        provider = self.providers.get(provider_name)
        rotator = self.rotators.get(rotator_name)
        
        if not provider:
            logger.error(f"Provider '{provider_name}' not found")
            return False
        
        if not rotator:
            logger.error(f"Rotator '{rotator_name}' not found")
            return False
        
        try:
            # Step 1: Get current secret (for backup/rollback)
            current_secret = provider.get_secret(secret_id)
            logger.info(f"Retrieved current secret for {secret_id}")
            
            # Step 2: Generate new secret
            new_secret = rotator.generate_new_secret()
            if not new_secret:
                logger.error(f"Failed to generate new secret for {job_name}")
                return False
            
            # Step 3: Validate new secret
            if not rotator.validate_secret(new_secret):
                logger.error(f"Generated secret failed validation for {job_name}")
                return False
            
            # Step 4: Update secret in provider
            success = provider.update_secret(secret_id, new_secret)
            if success:
                logger.info(f"Successfully rotated secret for {job_name}")
                return True
            else:
                logger.error(f"Failed to update secret for {job_name}")
                return False
                
        except Exception as e:
            logger.error(f"Error during rotation of {job_name}: {e}")
            return False
    
    def rotate_all_secrets(self) -> Dict[str, bool]:
        """Rotate all configured secrets"""
        results = {}
        logger.info(f"Starting rotation of {len(self.rotation_jobs)} secrets")
        
        for job in self.rotation_jobs:
            job_name = job['name']
            success = self.rotate_secret(job)
            results[job_name] = success
            
            # Add delay between rotations to avoid overwhelming systems
            time.sleep(1)
        
        successful = sum(1 for result in results.values() if result)
        logger.info(f"Rotation complete: {successful}/{len(results)} successful")
        
        return results
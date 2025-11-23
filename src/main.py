import sys
import time
import signal
from pathlib import Path

# Add src to path so we can import our modules
sys.path.append(str(Path(__file__).parent))

from config.settings import settings
from providers.file_provider import FileSecretProvider
from rotators.password_rotator import PasswordRotator
from rotation_engine import RotationEngine
from scheduler import RotationScheduler
from web_interface import WebServer
from utils.logger import logger
from encryption_manager import EncryptionManager


class SecretRotationApp:
    """Main application class with encryption support"""

    def __init__(self):
        self.engine = None
        self.scheduler = None
        self.web_server = None
        self.encryption_manager = None
        self.running = False

    def setup(self):
        """Set up the application components"""
        logger.info("Setting up Secret Rotation System")
        
        # Initialize encryption manager if enabled
        encryption_enabled = settings.get('security.encryption.enabled', True)
        if encryption_enabled:
            key_file = settings.get('security.encryption.key_file', 'config/.master.key')
            self.encryption_manager = EncryptionManager(key_file=key_file)
            logger.info("Encryption initialized")
        else:
            logger.warning("Encryption is DISABLED - secrets will be stored in plaintext!")
        
        # Initialize rotation engine
        self.engine = RotationEngine()
        
        # Configure backup manager with encryption
        encrypt_backups = settings.get('backup.encrypt_backups', True)
        backup_dir = settings.get('providers.file_storage.backup_path', 'data/backup')
        
        from backup_manager import BackupManager
        self.engine.backup_manager = BackupManager(
            backup_dir=backup_dir,
            encrypt_backups=encrypt_backups
        )
        
        # Set up providers with encryption settings
        encrypt_secrets = settings.get('providers.file_storage.encrypt_secrets', True)
        file_provider = FileSecretProvider(
            name="file_storage",
            config={
                "file_path": settings.get('providers.file_storage.file_path', 'data/secrets.json'),
                "encrypt_secrets": encrypt_secrets,
                "encryption_key_file": settings.get('security.encryption.master_key_file', 'config/.master.key')
            }
        )
        self.engine.register_provider(file_provider)
        
        # Validate provider connection (includes encryption test)
        if file_provider.validate_connection():
            logger.info("Provider connection validated (encryption working)")
        else:
            logger.error("Provider connection validation failed!")
        
        # Set up rotators
        password_rotator = PasswordRotator(
            name="password_gen",
            config=settings.get('rotators.password_gen', {
                "length": 16,
                "use_symbols": True,
                "use_numbers": True,
                "use_uppercase": True,
                "use_lowercase": True
            })
        )
        self.engine.register_rotator(password_rotator)
        
        # Add rotation jobs from config
        jobs = settings.get('jobs', [])
        if jobs:
            for job in jobs:
                self.engine.add_rotation_job(job)
            logger.info(f"Loaded {len(jobs)} rotation jobs from config")
        else:
            # Fallback to hardcoded jobs if no config
            default_jobs = [
                {
                    "name": "database_password",
                    "provider": "file_storage",
                    "rotator": "password_gen",
                    "secret_id": "db_password"
                },
                {
                    "name": "api_key",
                    "provider": "file_storage",
                    "rotator": "password_gen",
                    "secret_id": "api_key"
                },
                {
                    "name": "service_token",
                    "provider": "file_storage",
                    "rotator": "password_gen", 
                    "secret_id": "service_token"
                }
            ]
            
            for job in default_jobs:
                self.engine.add_rotation_job(job)
            logger.info(f"Using {len(default_jobs)} default rotation jobs")
        
        # Set up scheduler
        self.scheduler = RotationScheduler(
            rotation_function=self.engine.rotate_all_secrets,
            backup_manager=self.engine.backup_manager
        )
        schedule_config = settings.get('rotation.schedule', 'daily')
        self.scheduler.setup_schedule(schedule_config)
        
        # Set up web server
        web_port = settings.get('web.port', 8080)
        self.web_server = WebServer(self.engine, port=web_port)
        
        logger.info("Setup complete")
        self._print_security_status()

    def _print_security_status(self):
        """Print security configuration status"""
        logger.info("=" * 60)
        logger.info("SECURITY STATUS")
        logger.info("=" * 60)
        
        encryption_enabled = settings.get('security.encryption.enabled', True)
        encrypt_secrets = settings.get('providers.file_storage.encrypt_secrets', True)
        encrypt_backups = settings.get('backup.encrypt_backups', True)
        
        logger.info(f"Encryption System: {'ENABLED' if encryption_enabled else 'DISABLED'}")
        logger.info(f"Secret Storage Encryption: {'ENABLED' if encrypt_secrets else 'DISABLED'}")
        logger.info(f"Backup Encryption: {'ENABLED' if encrypt_backups else 'DISABLED'}")
        
        if encryption_enabled:
            key_file = settings.get('security.encryption.master_key_file', 'config/.master.key')
            key_path = Path(key_file)
            if key_path.exists():
                logger.info(f"Master Key: {key_file} (EXISTS)")
                logger.warning("IMPORTANT: Backup this key file securely!")
            else:
                logger.info(f"Master Key: {key_file} (WILL BE GENERATED)")
        
        logger.info("=" * 60)

    def start(self):
        """Start all components"""
        if not self.engine:
            self.setup()
        
        self.running = True
        
        # Start scheduler
        self.scheduler.start()
        
        # Start web server
        self.web_server.start()
        
        logger.info("Secret Rotation System started")
        logger.info(f"Web interface available at: http://localhost:{settings.get('web.port', 8080)}")
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Keep the main thread alive
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """Stop all components"""
        logger.info("Shutting down Secret Rotation System")
        
        self.running = False
        
        if self.scheduler:
            self.scheduler.stop()
        
        if self.web_server:
            self.web_server.stop()
        
        logger.info("Shutdown complete")

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
    
    def run_once(self):
        """Run rotation once (for testing or manual execution)"""
        if not self.engine:
            self.setup()
        
        logger.info("Running one-time secret rotation")
        results = self.engine.rotate_all_secrets()
        
        print("\nRotation Results:")
        for job_name, success in results.items():
            status = "SUCCESS" if success else "FAILED"
            print(f"  {job_name}: {status}")
        
        return results
    
    def migrate_to_encrypted(self):
        """
        Migrate existing plaintext secrets to encrypted format.
        Use this when enabling encryption on an existing deployment.
        """
        if not self.engine:
            self.setup()
        
        logger.info("Starting migration to encrypted storage")
        
        for provider_name, provider in self.engine.providers.items():
            if hasattr(provider, 'migrate_to_encrypted'):
                logger.info(f"Migrating provider: {provider_name}")
                success = provider.migrate_to_encrypted()
                if success:
                    logger.info(f"Successfully migrated {provider_name}")
                else:
                    logger.error(f"Failed to migrate {provider_name}")
            else:
                logger.warning(f"Provider {provider_name} does not support migration")
        
        logger.info("Migration complete")
    
    def verify_encryption(self):
        """Verify that encryption is working correctly"""
        if not self.engine:
            self.setup()
        
        logger.info("Verifying encryption setup")
        
        # Test encryption manager
        if self.encryption_manager:
            try:
                test_value = "test_secret_value_123"
                encrypted = self.encryption_manager.encrypt(test_value)
                decrypted = self.encryption_manager.decrypt(encrypted)
                
                if decrypted == test_value:
                    logger.info("✓ Encryption manager working correctly")
                else:
                    logger.error("✗ Encryption verification failed: decryption mismatch")
                    return False
            except Exception as e:
                logger.error(f"✗ Encryption verification failed: {e}")
                return False
        else:
            logger.warning("Encryption manager not initialized")
            return False
        
        # Test provider encryption
        for provider_name, provider in self.engine.providers.items():
            if hasattr(provider, 'validate_connection'):
                if provider.validate_connection():
                    logger.info(f"✓ Provider {provider_name} encryption working")
                else:
                    logger.error(f"✗ Provider {provider_name} encryption check failed")
                    return False
        
        logger.info("All encryption checks passed")
        return True


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Secret Rotation System')
    parser.add_argument('--mode', choices=['daemon', 'once', 'migrate', 'verify'], 
                       default='daemon',
                       help='Run mode: daemon (with scheduler), once (single rotation), '
                            'migrate (convert to encrypted), verify (test encryption)')
    
    args = parser.parse_args()
    
    app = SecretRotationApp()
    
    if args.mode == 'once':
        app.run_once()
    elif args.mode == 'migrate':
        app.migrate_to_encrypted()
    elif args.mode == 'verify':
        app.verify_encryption()
    else:
        app.start()

if __name__ == "__main__":
    main()
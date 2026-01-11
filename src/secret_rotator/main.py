import sys
import time
import signal
import argparse
from pathlib import Path

from secret_rotator.config.settings import settings
from secret_rotator.providers.file_provider import FileSecretProvider
from secret_rotator.rotators.password_rotator import PasswordRotator
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.scheduler import RotationScheduler
from secret_rotator.web_interface import WebServer
from secret_rotator.utils.logger import logger
from secret_rotator.encryption_manager import EncryptionManager
from secret_rotator.backup_manager import BackupManager


class SecretRotationApp:
    """Main application class with encryption support"""

    def __init__(self):
        self.engine = None
        self.scheduler = None
        self.web_server = None
        self.encryption_manager = None
        self.backup_manager = None
        self.running = False

    def setup(self):
        """Set up the application components"""
        logger.info("Setting up Secret Rotation System")

        # Initialize encryption manager if enabled
        encryption_enabled = settings.get("security.encryption.enabled", True)
        if encryption_enabled:
            key_file = settings.get("security.encryption.master_key_file", "config/.master.key")
            self.encryption_manager = EncryptionManager(key_file=key_file)
            logger.info("Encryption initialized")

            # Check if master key needs rotation
            rotate_days = settings.get("security.encryption.rotate_master_key_days", 90)
            if self.encryption_manager.should_rotate_key(rotate_days):
                logger.warning(
                    f"Master key is older than {rotate_days} days and should be rotated. "
                    "Run: secret-rotator --mode rotate-master-key"
                )
        else:
            logger.warning("Encryption is DISABLED - secrets will be stored in plaintext!")

        # Initialize backup manager
        encrypt_backups = settings.get("backup.encrypt_backups", True)
        backup_dir = settings.get("backup.storage_path", "data/backup")
        self.backup_manager = BackupManager(backup_dir=backup_dir, encrypt_backups=encrypt_backups)

        # Initialize rotation engine
        self.engine = RotationEngine()
        self.engine.backup_manager = self.backup_manager

        self._setup_providers()
        self._setup_rotators()
        self._setup_rotation_jobs()

        # Set up scheduler with backup manager
        schedule_config = settings.get("rotation.schedule", "daily")
        self.scheduler = RotationScheduler(
            rotation_function=self.engine.rotate_all_secrets, backup_manager=self.backup_manager
        )
        self.scheduler.setup_schedule(schedule_config)

        # Set up web server
        web_enabled = settings.get("web.enabled", True)
        if web_enabled:
            web_port = settings.get("web.port", 8080)
            web_host = settings.get("web.host", "localhost")
            self.web_server = WebServer(self.engine, port=web_port)
            # Store scheduler reference in engine for web interface access
            self.engine.scheduler = self.scheduler

        logger.info("Setup complete")
        self._print_security_status()
        self._print_backup_health()

    def _setup_providers(self):
        """Set up secret providers from configuration"""
        providers_config = settings.get("providers", {})

        for provider_name, provider_config in providers_config.items():
            provider_type = provider_config.get("type")

            if provider_type == "file":
                encrypt_secrets = settings.get("security.encryption.enabled", True)
                file_provider = FileSecretProvider(
                    name=provider_name,
                    config={
                        "file_path": provider_config.get("file_path", "data/secrets.json"),
                        "encrypt_secrets": encrypt_secrets,
                        "encryption_key_file": settings.get(
                            "security.encryption.master_key_file", "config/.master.key"
                        ),
                    },
                )
                self.engine.register_provider(file_provider)

                # Validate provider connection
                if file_provider.validate_connection():
                    logger.info(f"Provider '{provider_name}' validated successfully")
                else:
                    logger.error(f"Provider '{provider_name}' validation failed!")

            # Add support for other provider types here (AWS, Azure, etc.)
            elif provider_type == "aws":
                logger.warning(f"AWS provider '{provider_name}' not yet implemented")
            else:
                logger.warning(f"Unknown provider type '{provider_type}' for '{provider_name}'")

    def _setup_rotators(self):
        """Set up secret rotators from configuration"""
        rotators_config = settings.get("rotators", {})

        for rotator_name, rotator_config in rotators_config.items():
            rotator_type = rotator_config.get("type")

            if rotator_type == "password":
                password_rotator = PasswordRotator(name=rotator_name, config=rotator_config)
                self.engine.register_rotator(password_rotator)

            # Add support for other rotator types
            elif rotator_type == "api_key":
                from secret_rotator.rotators.advanced_rotators import APIKeyRotator

                api_rotator = APIKeyRotator(name=rotator_name, config=rotator_config)
                self.engine.register_rotator(api_rotator)

            elif rotator_type == "jwt_secret":
                from secret_rotator.rotators.advanced_rotators import JWTSecretRotator

                jwt_rotator = JWTSecretRotator(name=rotator_name, config=rotator_config)
                self.engine.register_rotator(jwt_rotator)

            else:
                logger.warning(f"Unknown rotator type '{rotator_type}' for '{rotator_name}'")

    def _setup_rotation_jobs(self):
        """Set up rotation jobs from configuration"""
        jobs = settings.get("jobs", [])

        if jobs:
            for job in jobs:
                if self.engine.add_rotation_job(job):
                    logger.debug(f"Added job: {job['name']}")
            logger.info(f"Loaded {len(jobs)} rotation jobs from config")
        else:
            logger.warning("No rotation jobs configured. Add jobs to config/config.yaml")

    def _print_security_status(self):
        """Print security configuration status"""
        logger.info("=" * 60)
        logger.info("SECURITY STATUS")
        logger.info("=" * 60)

        encryption_enabled = settings.get("security.encryption.enabled", True)
        encrypt_backups = settings.get("backup.encrypt_backups", True)

        logger.info(f"Encryption System: {'ENABLED' if encryption_enabled else 'DISABLED'}")
        logger.info(f"Backup Encryption: {'ENABLED' if encrypt_backups else 'DISABLED'}")

        if encryption_enabled and self.encryption_manager:
            key_info = self.encryption_manager.get_key_info()
            logger.info(f"Master Key ID: {key_info.get('key_id', 'unknown')}")
            logger.info(f"Key Age: {key_info.get('age_days', 'unknown')} days")

            key_file = settings.get("security.encryption.master_key_file", "config/.master.key")
            logger.warning(f"IMPORTANT: Backup master key file: {key_file}")
            logger.info("Use: python tools/manage_key_backups.py create-encrypted")

        logger.info("=" * 60)

    def _print_backup_health(self):
        """Print backup system health status"""
        if not self.backup_manager:
            return

        metadata = self.backup_manager.export_backup_metadata()

        logger.info("=" * 60)
        logger.info("BACKUP SYSTEM STATUS")
        logger.info("=" * 60)
        logger.info(f"Total Backups: {metadata['total_backups']}")
        logger.info(f"Secrets with Backups: {metadata['secrets_with_backups']}")
        logger.info(f"Backup Directory: {metadata['backup_directory']}")

        if metadata["total_backups"] > 0:
            logger.info(f"Oldest Backup: {metadata.get('oldest_backup', 'N/A')}")
            logger.info(f"Newest Backup: {metadata.get('newest_backup', 'N/A')}")

        logger.info("=" * 60)

    def start(self):
        """Start all components"""
        if not self.engine:
            self.setup()

        self.running = True

        # Start scheduler
        self.scheduler.start()

        # Start web server if enabled
        if self.web_server:
            self.web_server.start()
            logger.info(
                f"Web interface: http://{settings.get('web.host', 'localhost')}:{settings.get('web.port', 8080)}"
            )

        logger.info("Secret Rotation System started")
        logger.info("Press Ctrl+C to stop")

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

        print("\n" + "=" * 60)
        print("ROTATION RESULTS")
        print("=" * 60)

        successful = sum(1 for success in results.values() if success)

        for job_name, success in results.items():
            status = "✓ SUCCESS" if success else "✗ FAILED"
            print(f"  {job_name}: {status}")

        print("=" * 60)
        print(f"Summary: {successful}/{len(results)} successful")
        print("=" * 60)

        return results

    def migrate_to_encrypted(self):
        """
        Migrate existing plaintext secrets to encrypted format.
        Use this when enabling encryption on an existing deployment.
        """
        if not self.engine:
            self.setup()

        logger.info("Starting migration to encrypted storage")
        print("\n" + "=" * 60)
        print("MIGRATE TO ENCRYPTED STORAGE")
        print("=" * 60)
        print("\nThis will encrypt all plaintext secrets in your providers.")
        print("Original secrets will be preserved if migration fails.")

        response = input("\nContinue? (yes/no): ")
        if response.lower() != "yes":
            print("Migration cancelled.")
            return

        migrated_count = 0
        failed_count = 0

        for provider_name, provider in self.engine.providers.items():
            if hasattr(provider, "migrate_to_encrypted"):
                logger.info(f"Migrating provider: {provider_name}")
                success = provider.migrate_to_encrypted()
                if success:
                    logger.info(f"Successfully migrated {provider_name}")
                    migrated_count += 1
                else:
                    logger.error(f"Failed to migrate {provider_name}")
                    failed_count += 1
            else:
                logger.warning(f"Provider {provider_name} does not support migration")

        print("\n" + "=" * 60)
        print(f"Migration complete: {migrated_count} providers migrated, {failed_count} failed")
        print("=" * 60)

    def verify_encryption(self):
        """Verify that encryption is working correctly"""
        if not self.engine:
            self.setup()

        logger.info("Verifying encryption setup")
        print("\n" + "=" * 60)
        print("ENCRYPTION VERIFICATION")
        print("=" * 60)

        all_passed = True

        # Test encryption manager
        if self.encryption_manager:
            try:
                test_value = "test_secret_value_123"
                encrypted = self.encryption_manager.encrypt(test_value)
                decrypted = self.encryption_manager.decrypt(encrypted)

                if decrypted == test_value:
                    print("✓ Encryption manager: PASSED")
                    logger.info("Encryption manager working correctly")
                else:
                    print("✗ Encryption manager: FAILED (decryption mismatch)")
                    logger.error("Encryption verification failed: decryption mismatch")
                    all_passed = False
            except Exception as e:
                print(f"✗ Encryption manager: FAILED ({e})")
                logger.error(f"Encryption verification failed: {e}")
                all_passed = False
        else:
            print("✗ Encryption manager: NOT INITIALIZED")
            logger.warning("Encryption manager not initialized")
            all_passed = False

        # Test provider encryption
        for provider_name, provider in self.engine.providers.items():
            if hasattr(provider, "validate_connection"):
                if provider.validate_connection():
                    print(f"✓ Provider '{provider_name}': PASSED")
                    logger.info(f"Provider {provider_name} encryption working")
                else:
                    print(f"✗ Provider '{provider_name}': FAILED")
                    logger.error(f"Provider {provider_name} encryption check failed")
                    all_passed = False

        # Test backup encryption
        if self.backup_manager and self.backup_manager.encrypt_backups:
            try:
                test_backup = self.backup_manager.create_backup(
                    "test_verification", "old_test_value", "new_test_value"
                )
                backup_data = self.backup_manager.restore_backup(test_backup, decrypt=True)

                if backup_data["old_value"] == "old_test_value":
                    print("✓ Backup encryption: PASSED")
                    logger.info("Backup encryption working")
                    # Clean up test backup
                    Path(test_backup).unlink()
                else:
                    print("✗ Backup encryption: FAILED")
                    all_passed = False
            except Exception as e:
                print(f"✗ Backup encryption: FAILED ({e})")
                logger.error(f"Backup encryption check failed: {e}")
                all_passed = False

        print("=" * 60)
        if all_passed:
            print("All encryption checks PASSED")
            return True
        else:
            print("Some encryption checks FAILED")
            return False

    def verify_backups(self):
        """Run backup integrity verification"""
        if not self.engine:
            self.setup()

        if not self.scheduler:
            logger.error("Scheduler not initialized")
            return

        logger.info("Running backup integrity verification")
        print("\n" + "=" * 60)
        print("BACKUP INTEGRITY VERIFICATION")
        print("=" * 60)

        report = self.scheduler.run_verification_now()

        print(f"\nTotal Backups: {report['total_backups']}")
        print(f"Verified: {report['verified']}")
        print(f"Failed: {report['failed']}")

        if report["failed"] > 0:
            print(f"\n⚠️  WARNING: {report['failed']} backup(s) failed verification!")
            print("Corrupted backups:")
            for corrupted in report.get("corrupted", []):
                print(f"  - {corrupted['backup_file']}")
        else:
            print("\n✓ All backups verified successfully")

        print("=" * 60)

    def rotate_master_key(self):
        """Rotate the master encryption key"""
        if not self.engine:
            self.setup()

        if not self.encryption_manager:
            logger.error("Encryption not enabled")
            print("ERROR: Encryption is not enabled. Cannot rotate master key.")
            return

        print("\n" + "=" * 60)
        print("MASTER KEY ROTATION")
        print("=" * 60)
        print("\n⚠️  WARNING: This is a critical operation!")
        print("\nThis will:")
        print("  1. Generate a new master encryption key")
        print("  2. Re-encrypt ALL secrets with the new key")
        print("  3. Backup the old key")
        print("\nBefore proceeding:")
        print("  - Create a backup of your current master key")
        print("  - Ensure all backups are verified and accessible")
        print("  - Run during a maintenance window")

        response = input("\nHave you created a backup of the master key? (yes/no): ")
        if response.lower() != "yes":
            print("\nCreate a backup first:")
            print("  python tools/manage_key_backups.py create-encrypted")
            return

        response = input("\nContinue with master key rotation? (yes/no): ")
        if response.lower() != "yes":
            print("Rotation cancelled.")
            return

        # Define re-encryption callback
        def re_encrypt_all_secrets(old_cipher, new_cipher):
            """Re-encrypt all secrets with new key"""
            try:
                for provider_name, provider in self.engine.providers.items():
                    if hasattr(provider, "encryption_manager"):
                        logger.info(f"Re-encrypting secrets in provider: {provider_name}")

                        # Temporarily use old cipher to decrypt
                        old_em = provider.encryption_manager
                        provider.encryption_manager.cipher = old_cipher

                        # Get all secrets (decrypted)
                        import json

                        with open(provider.file_path, "r") as f:
                            secrets = json.load(f)

                        # Re-encrypt with new cipher
                        provider.encryption_manager.cipher = new_cipher

                        for secret_id in secrets.keys():
                            # Decrypt with old key
                            provider.encryption_manager.cipher = old_cipher
                            decrypted_value = provider.get_secret(secret_id)

                            # Encrypt with new key
                            provider.encryption_manager.cipher = new_cipher
                            provider.update_secret(secret_id, decrypted_value)

                        logger.info(f"Re-encrypted {len(secrets)} secrets in {provider_name}")

                return True
            except Exception as e:
                logger.error(f"Re-encryption failed: {e}")
                return False

        # Perform rotation
        logger.info("Starting master key rotation...")
        success = self.encryption_manager.rotate_master_key(
            re_encrypt_callback=re_encrypt_all_secrets
        )

        if success:
            print("\n✓ SUCCESS: Master key rotated successfully")
            print("\nNext steps:")
            print("  1. Create a new backup of the master key")
            print("  2. Update key backups in all locations")
            print("  3. Restart the application")
            print("  4. Verify encryption: secret-rotator --mode verify")
        else:
            print("\n✗ ERROR: Master key rotation failed")
            print("Old key has been restored from backup.")

    def cleanup_old_backups(self):
        """Manually trigger backup cleanup"""
        if not self.engine:
            self.setup()

        if not self.backup_manager:
            logger.error("Backup manager not initialized")
            return

        days_to_keep = settings.get("backup.retention.days", 90)

        print("\n" + "=" * 60)
        print("BACKUP CLEANUP")
        print("=" * 60)
        print(f"\nThis will remove backups older than {days_to_keep} days.")

        response = input("\nContinue? (yes/no): ")
        if response.lower() != "yes":
            print("Cleanup cancelled.")
            return

        removed = self.backup_manager.cleanup_old_backups(days_to_keep)
        print(f"\n✓ Removed {removed} old backup(s)")

    def show_status(self):
        """Show application status and health"""
        if not self.engine:
            self.setup()

        print("\n" + "=" * 60)
        print("SECRET ROTATION SYSTEM STATUS")
        print("=" * 60)

        # System info
        print(f"\nProviders: {len(self.engine.providers)}")
        for name in self.engine.providers.keys():
            print(f"  - {name}")

        print(f"\nRotators: {len(self.engine.rotators)}")
        for name in self.engine.rotators.keys():
            print(f"  - {name}")

        print(f"\nRotation Jobs: {len(self.engine.rotation_jobs)}")
        for job in self.engine.rotation_jobs:
            print(f"  - {job['name']} ({job['secret_id']})")

        # Encryption status
        if self.encryption_manager:
            key_info = self.encryption_manager.get_key_info()
            print("\nEncryption: ENABLED")
            print(f"  Key ID: {key_info.get('key_id', 'unknown')}")
            print(f"  Key Age: {key_info.get('age_days', 'unknown')} days")
        else:
            print("\nEncryption: DISABLED")

        # Backup status
        if self.backup_manager:
            metadata = self.backup_manager.export_backup_metadata()
            print(f"\nBackups: {metadata['total_backups']} total")
            print(f"  Secrets with backups: {metadata['secrets_with_backups']}")
            print(f"  Encryption: {'ENABLED' if metadata['encryption_enabled'] else 'DISABLED'}")

        # Scheduler status
        if self.scheduler:
            print(f"\nScheduler: {'RUNNING' if self.scheduler.running else 'STOPPED'}")
            print(f"  Schedule: {settings.get('rotation.schedule', 'daily')}")

        print("=" * 60)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Secret Rotation System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start the daemon with scheduler and web interface
  secret-rotator

  # Run a single rotation manually
  secret-rotator --mode once

  # Verify encryption is working
  secret-rotator --mode verify

  # Show system status
  secret-rotator --mode status

  # Migrate existing plaintext secrets to encrypted
  secret-rotator --mode migrate

  # Verify backup integrity
  secret-rotator --mode verify-backups

  # Rotate master encryption key
  secret-rotator --mode rotate-master-key

  # Cleanup old backups
  secret-rotator --mode cleanup-backups
        """,
    )

    parser.add_argument(
        "--mode",
        choices=[
            "daemon",
            "once",
            "migrate",
            "verify",
            "verify-backups",
            "rotate-master-key",
            "cleanup-backups",
            "status",
        ],
        default="daemon",
        help="Run mode (default: daemon)",
    )

    parser.add_argument("--config", help="Path to config file (default: config/config.yaml)")

    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Override config path if specified
    if args.config:
        from secret_rotator.config.settings import settings

        settings.config_path = Path(args.config)
        settings.config = settings.load_config()

    # Set debug logging if requested
    if args.debug:
        import logging
        from secret_rotator.utils.logger import logger

        logger.setLevel(logging.DEBUG)
        logger.info("Debug logging enabled")

    # Create and run app
    app = SecretRotationApp()

    try:
        if args.mode == "once":
            app.run_once()
        elif args.mode == "migrate":
            app.migrate_to_encrypted()
        elif args.mode == "verify":
            success = app.verify_encryption()
            sys.exit(0 if success else 1)
        elif args.mode == "verify-backups":
            app.verify_backups()
        elif args.mode == "rotate-master-key":
            app.rotate_master_key()
        elif args.mode == "cleanup-backups":
            app.cleanup_old_backups()
        elif args.mode == "status":
            app.show_status()
        else:  # daemon mode
            app.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

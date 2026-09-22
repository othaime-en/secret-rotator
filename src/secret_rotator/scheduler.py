import schedule
import time
import threading
from typing import Callable
from secret_rotator.utils.logger import logger
from secret_rotator.config.settings import settings
from secret_rotator.backup_manager import BackupManager, BackupIntegrityChecker
from secret_rotator.rotation_engine import RotationInProgressError


class RotationScheduler:
    """Handle scheduled secret rotations and backup verification"""

    def __init__(self, rotation_function: Callable, backup_manager: BackupManager):
        self.rotation_function = rotation_function
        self.backup_manager = backup_manager
        self.running = False
        self.thread = None

        self.integrity_checker = BackupIntegrityChecker(backup_manager)

    def setup_schedule(self, schedule_config: str):
        """Set up rotation schedule and backup verification"""
        schedule.clear()

        if schedule_config == "daily":
            schedule.every().day.at("02:00").do(self._run_rotation)
        elif schedule_config == "weekly":
            schedule.every().week.do(self._run_rotation)
        elif schedule_config.startswith("every_"):
            # Format: "every_30_minutes" or "every_2_hours"
            parts = schedule_config.split("_")
            if len(parts) >= 3:
                interval = int(parts[1])
                unit = parts[2]
                if unit == "minutes":
                    schedule.every(interval).minutes.do(self._run_rotation)
                elif unit == "hours":
                    schedule.every(interval).hours.do(self._run_rotation)

        # Schedule backup cleanup (daily at 03:00)
        cleanup_time = settings.get("backup.cleanup_time", "03:00")
        schedule.every().day.at(cleanup_time).do(self._cleanup_backups)

        # Schedule backup integrity verification (daily at 04:00)
        verification_time = settings.get("backup.verification_time", "04:00")
        verification_enabled = settings.get("backup.verify_integrity", True)

        if verification_enabled:
            schedule.every().day.at(verification_time).do(self._verify_backup_integrity)
            logger.info(f"Scheduled backup verification: daily at {verification_time}")

        # Schedule weekly full backup verification (Sundays at 05:00)
        full_verification_enabled = settings.get("backup.full_verification_enabled", True)
        if full_verification_enabled:
            schedule.every().sunday.at("05:00").do(self._verify_all_backups_full)
            logger.info("Scheduled full backup verification: weekly on Sundays at 05:00")

        # Schedule checksum verification (every 6 hours)
        checksum_verification_enabled = settings.get("backup.checksum_verification_enabled", True)
        if checksum_verification_enabled:
            schedule.every(6).hours.do(self._verify_backup_checksums)
            logger.info("Scheduled checksum verification: every 6 hours")

        # Schedule remote backup sync, if configured (Phase 3). This is
        # what catches backups made before remote sync was turned on,
        # and anything upload_on_create missed due to a transient S3
        # failure — see remote_backup.py. A no-op (never scheduled) when
        # backup.remote_backup.enabled is false, so this adds nothing
        # for deployments that haven't set up off-host backups.
        if settings.get("backup.remote_backup.enabled", False):
            remote_sync_time = settings.get("backup.remote_backup.sync_time", "01:00")
            schedule.every().day.at(remote_sync_time).do(self._sync_remote_backups)
            logger.info(f"Scheduled remote backup sync: daily at {remote_sync_time}")

        logger.info(f"Scheduled rotation: {schedule_config}")
        logger.info(f"Scheduled backup cleanup: daily at {cleanup_time}")

    def _run_rotation(self):
        """Internal method to run rotation with error handling"""
        try:
            logger.info("Scheduled rotation starting")
            results = self.rotation_function()
            successful = sum(1 for result in results.values() if result)
            logger.info(f"Scheduled rotation complete: {successful}/{len(results)} successful")

            # If there were failures, run backup verification to ensure backups are intact
            if successful < len(results):
                logger.info("Some rotations failed, verifying backup integrity")
                self._verify_backup_integrity()

        except RotationInProgressError:
            # A manually-triggered rotation (e.g. the dashboard's
            # "Rotate All" button) was already running when this tick
            # fired. Skip this run rather than raising — the next
            # scheduled tick will pick it back up.
            logger.warning(
                "Skipped scheduled rotation: a manually-triggered rotation "
                "was already in progress"
            )
        except Exception as e:
            logger.error(f"Error in scheduled rotation: {e}")

    def _cleanup_backups(self):
        """Internal method to clean up old backups"""
        try:
            days_to_keep = settings.get("backup.retention.days", 90)
            removed_count = self.backup_manager.cleanup_old_backups(days_to_keep)
            logger.info(
                f"Scheduled backup cleanup completed: "
                f"removed {removed_count} old backups, "
                f"kept backups for {days_to_keep} days"
            )
        except Exception as e:
            logger.error(f"Error in scheduled backup cleanup: {e}")

    def _verify_backup_integrity(self):
        """Run backup integrity verification"""
        try:
            logger.info("Starting scheduled backup integrity verification")
            report = self.integrity_checker.verify_all_backups()

            # Log summary
            logger.info(
                f"Backup verification complete: "
                f"{report['verified']}/{report['total_backups']} verified, "
                f"{report['failed']} failed"
            )

            # If there are failures, alert
            if report["failed"] > 0:
                logger.error(f"ALERT: {report['failed']} backup(s) failed verification!")

                health = self.integrity_checker.get_backup_health_metrics()
                logger.error(f"Backup system health: {health['status']}")

        except Exception as e:
            logger.error(f"Error in scheduled backup verification: {e}")

    def _verify_all_backups_full(self):
        """Run full backup verification (more thorough, weekly)"""
        try:
            logger.info("Starting scheduled FULL backup integrity verification")

            # Run full verification with decryption
            report = self.integrity_checker.verify_all_backups()

            # Also verify checksums
            checksum_report = self.integrity_checker.verify_backup_checksums()

            logger.info(
                f"Full backup verification complete:\n"
                f"  Integrity: {report['verified']}/{report['total_backups']} verified\n"
                f"  Checksums: {checksum_report['checksum_matches']}/{checksum_report['backups_checked']} matched"
            )

            # Generate health report
            health = self.integrity_checker.get_backup_health_metrics()
            logger.info(f"Backup system health: {health}")

            if health["status"] != "healthy":
                logger.warning(
                    f"Backup system health is {health['status']} - "
                    f"success rate: {health['success_rate']}%"
                )

        except Exception as e:
            logger.error(f"Error in scheduled full backup verification: {e}")

    def _verify_backup_checksums(self):
        """Run quick checksum verification"""
        try:
            logger.info("Starting scheduled backup checksum verification")
            report = self.integrity_checker.verify_backup_checksums()

            logger.info(
                f"Checksum verification complete: "
                f"{report['checksum_matches']} matches, "
                f"{report['checksum_mismatches']} mismatches"
            )

            if report["checksum_mismatches"] > 0:
                logger.error(
                    f"ALERT: {report['checksum_mismatches']} backup(s) "
                    f"have checksum mismatches!"
                )

        except Exception as e:
            logger.error(f"Error in scheduled checksum verification: {e}")

    def _sync_remote_backups(self):
        """Run a bulk remote-backup sync (Phase 3). Best-effort by
        design — see BackupManager.sync_to_remote() /
        RemoteBackupClient: a failure here is logged and never allowed
        to affect rotations, local backups, or anything else the
        scheduler does."""
        try:
            logger.info("Starting scheduled remote backup sync")
            report = self.backup_manager.sync_to_remote()
            if report is None:
                # Remote backup was disabled between schedule setup and
                # this tick firing (e.g. config reloaded) — nothing to do.
                return
            logger.info(
                f"Remote backup sync complete: {report['uploaded']} uploaded, "
                f"{report['already_synced']} already synced, "
                f"{report['failed']} failed"
            )
            if report["failed"] > 0:
                logger.warning(
                    f"{report['failed']} backup(s) failed to sync to remote storage: "
                    f"{report['failed_files']}. Local backups are unaffected; "
                    f"these will be retried on the next scheduled sync."
                )
        except Exception as e:
            logger.error(f"Error in scheduled remote backup sync: {e}")

    def start(self):
        """Start the scheduler in a background thread"""
        if self.running:
            logger.warning("Scheduler already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()
        logger.info("Rotation scheduler started")

    def stop(self):
        """Stop the scheduler"""
        self.running = False
        if self.thread:
            self.thread.join()
        logger.info("Rotation scheduler stopped")

    def _run_scheduler(self):
        """Internal scheduler loop"""
        while self.running:
            schedule.run_pending()
            time.sleep(60)  # Check every minute

    def run_verification_now(self):
        """Run backup verification immediately (for manual triggering)"""
        logger.info("Running manual backup verification")
        return self.integrity_checker.verify_all_backups()

    def get_verification_history(self, days: int = 30):
        """Get backup verification history"""
        return self.integrity_checker.get_verification_history(days)

    def get_backup_health(self):
        """Get current backup health metrics"""
        return self.integrity_checker.get_backup_health_metrics()
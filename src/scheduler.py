import schedule
import time
import threading
from typing import Callable
from utils.logger import logger
from config.settings import settings
from backup_manager import BackupManager, BackupIntegrityChecker

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
        cleanup_time = settings.get('backup.cleanup_time', "03:00")
        schedule.every().day.at(cleanup_time).do(self._cleanup_backups)
        
        # Schedule backup integrity verification (daily at 04:00)
        verification_time = settings.get('backup.verification_time', "04:00")
        verification_enabled = settings.get('backup.verify_integrity', True)
        
        if verification_enabled:
            schedule.every().day.at(verification_time).do(self._verify_backup_integrity)
            logger.info(f"Scheduled backup verification: daily at {verification_time}")
        
        logger.info(f"Scheduled rotation: {schedule_config}")
        logger.info(f"Scheduled backup cleanup: daily at {cleanup_time}")
    
    def _run_rotation(self):
        """Internal method to run rotation with error handling"""
        try:
            logger.info("Scheduled rotation starting")
            results = self.rotation_function()
            successful = sum(1 for result in results.values() if result)
            logger.info(f"Scheduled rotation complete: {successful}/{len(results)} successful")
        except Exception as e:
            logger.error(f"Error in scheduled rotation: {e}")
    
    def _cleanup_backups(self):
        """Internal method to clean up old backups"""
        try:
            days_to_keep = settings.get('backup.retention.days', 90)
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
            if report['failed'] > 0:
                logger.error(
                    f"ALERT: {report['failed']} backup(s) failed verification!"
                )
                
                health = self.integrity_checker.get_backup_health_metrics()
                logger.error(f"Backup system health: {health['status']}")
            
        except Exception as e:
            logger.error(f"Error in scheduled backup verification: {e}")
    
    def start(self):
        """ Start the scheduler in a background thread"""
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
        """ Internal scheduler loop"""
        while self.running:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
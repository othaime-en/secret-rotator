import schedule
import time
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from secret_rotator.utils.logger import logger
from secret_rotator.config.settings import settings
from secret_rotator.backup_manager import BackupManager, BackupIntegrityChecker
from secret_rotator.rotation_engine import RotationInProgressError
from secret_rotator.schedule_spec import (
    DAILY_TIME,
    KIND_DAILY,
    KIND_MONTHLY,
    KIND_WEEKLY,
    ScheduleSpec,
    next_monthly,
    parse_schedule,
)

# Tag on the schedule jobs that trigger rotation, so the dashboard can ask
# when the next rotation is due without touching the maintenance jobs
# (cleanup, verification, remote sync) scheduled alongside it. Each
# distinct schedule also gets its own tag, ROTATION_TAG + ":" + its name.
ROTATION_TAG = "rotation"


class RotationScheduler:
    """Handle scheduled secret rotations and backup verification.

    Rotation schedules are per job. Every job follows the global
    ``rotation.schedule`` unless it has a ``schedule`` of its own, and
    the jobs that share a schedule are rotated together when it fires.
    When every job follows one schedule (the common case) that is a
    plain full rotation, exactly as before per-job schedules existed.
    """

    def __init__(
        self,
        rotation_function: Callable,
        backup_manager: BackupManager,
        rotate_jobs_function: Optional[Callable[[List[str]], Dict[str, bool]]] = None,
        jobs_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ):
        """
        Args:
            rotation_function: rotates every job (RotationEngine.rotate_all_secrets).
            backup_manager: for backup maintenance and verification.
            rotate_jobs_function: rotates the named jobs
                (RotationEngine.rotate_jobs). Needed only when some jobs
                have a schedule different from the default.
            jobs_provider: returns the configured jobs, read each time a
                schedule fires so jobs added later are picked up. Without
                it every schedule rotates every job, as in older versions.
        """
        self.rotation_function = rotation_function
        self.rotate_jobs_function = rotate_jobs_function
        self.jobs_provider = jobs_provider
        self.backup_manager = backup_manager
        self.running = False
        self.thread = None
        self.default_schedule = "daily"
        self._rotation_schedules: List[str] = []

        self.integrity_checker = BackupIntegrityChecker(backup_manager)

    # ---- schedule setup ----------------------------------------------------

    def setup_schedule(self, schedule_config: str):
        """Set up rotation schedules and backup maintenance.

        Raises:
            ValueError: the global schedule, or a job's own schedule, is
                not a valid schedule (see schedule_spec.py). Failing here
                is deliberate: an unrecognised schedule used to mean
                nothing was ever rotated, with no error.
        """
        default = parse_schedule(schedule_config)  # fail before touching anything
        self.default_schedule = default.name
        self._validated_schedules()  # same for per-job schedules

        schedule.clear()
        self._register_rotations()

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

        logger.info(f"Scheduled backup cleanup: daily at {cleanup_time}")

    def set_default_schedule(self, schedule_config: str):
        """Change the global default schedule while running: jobs that
        follow it are rescheduled, the rest and the maintenance schedules
        are left alone.

        Raises:
            ValueError: the schedule is invalid; nothing is changed.
        """
        default = parse_schedule(schedule_config)
        previous = self.default_schedule
        self.default_schedule = default.name
        try:
            self.refresh_rotation_schedules()
        except ValueError:
            self.default_schedule = previous
            raise

    def refresh_rotation_schedules(self):
        """Re-read the jobs and rebuild only the rotation schedules.

        Call after a job is added, changed or removed. Backup maintenance
        schedules are left alone.

        Raises:
            ValueError: a job has an invalid schedule; nothing is changed.
        """
        self._validated_schedules()
        schedule.clear(ROTATION_TAG)
        self._register_rotations()

    def effective_schedule(self, job: Dict[str, Any]) -> str:
        """The canonical schedule a job runs on (its own, else the default).

        Raises:
            ValueError: the job's own schedule is invalid.
        """
        return parse_schedule(job.get("schedule") or self.default_schedule).name

    def _jobs(self) -> List[Dict[str, Any]]:
        return list(self.jobs_provider()) if self.jobs_provider else []

    def _validated_schedules(self) -> List[str]:
        """Every distinct schedule in use, default first. Collects all
        problems into one error instead of stopping at the first."""
        names = [self.default_schedule]
        problems = []
        for job in self._jobs():
            try:
                name = self.effective_schedule(job)
            except ValueError as e:
                problems.append(f"job '{job.get('name', '?')}': {e}")
                continue
            if name not in names:
                names.append(name)
        if problems:
            raise ValueError("Invalid rotation schedule(s):\n  - " + "\n  - ".join(problems))
        return names

    def _register_rotations(self):
        names = self._validated_schedules()
        self._rotation_schedules = names
        for name in names:
            self._register_rotation(parse_schedule(name))
            logger.info(f"Scheduled rotation: {name}")

    def _register_rotation(self, spec: ScheduleSpec):
        tags = (ROTATION_TAG, f"{ROTATION_TAG}:{spec.name}")

        if spec.kind in (KIND_DAILY, KIND_MONTHLY):
            # "monthly" ticks daily and _run_rotation only acts on the 1st.
            schedule.every().day.at(DAILY_TIME).tag(*tags).do(self._run_rotation, spec.name)
        elif spec.kind == KIND_WEEKLY:
            schedule.every().week.tag(*tags).do(self._run_rotation, spec.name)
        else:
            # The schedule library wants "every(1).hour", not "every(1).hours".
            every = schedule.every(spec.interval)
            unit = spec.unit[:-1] if spec.interval == 1 else spec.unit
            getattr(every, unit).tag(*tags).do(self._run_rotation, spec.name)

    # ---- when will it run --------------------------------------------------

    def next_rotation_for(self, schedule_name: str) -> Optional[datetime]:
        """When the given schedule next fires (UTC), or None if it is not
        scheduled in this process."""
        try:
            spec = parse_schedule(schedule_name)
        except ValueError:
            return None

        entries = schedule.get_jobs(f"{ROTATION_TAG}:{spec.name}")
        if not entries:
            return None

        # schedule stores naive local times; astimezone() on a naive
        # datetime interprets it as local time.
        if spec.kind == KIND_MONTHLY:
            return next_monthly(datetime.now()).astimezone(timezone.utc)
        runs = [entry.next_run for entry in entries if entry.next_run]
        return min(runs).astimezone(timezone.utc) if runs else None

    def next_rotation_time(self) -> Optional[datetime]:
        """When the soonest rotation of any schedule fires (UTC), or None
        if no rotation schedule is set up in this process."""
        times = [self.next_rotation_for(name) for name in self._rotation_schedules]
        scheduled = [t for t in times if t is not None]
        return min(scheduled) if scheduled else None

    # ---- firing ------------------------------------------------------------

    def _jobs_due(self, schedule_name: str) -> Optional[List[str]]:
        """Names of the jobs on this schedule, or None meaning "all of
        them" (no per-job information, or every job is on it)."""
        if self.jobs_provider is None:
            return None
        jobs = self._jobs()
        due = []
        for job in jobs:
            if job.get("paused"):
                continue  # paused in the dashboard: no scheduled rotation
            try:
                if self.effective_schedule(job) == schedule_name:
                    due.append(job["name"])
            except ValueError:
                continue  # reported when the schedule was set up
        return None if len(due) == len(jobs) else due

    def _rotate_for(self, schedule_name: str) -> Dict[str, bool]:
        due = self._jobs_due(schedule_name)
        if due is None:
            return dict(self.rotation_function())
        if not due:
            return {}
        if self.rotate_jobs_function is None:
            logger.error(
                f"Schedule '{schedule_name}' covers only some jobs but no "
                "rotate_jobs_function was given; skipping this run"
            )
            return {}
        return self.rotate_jobs_function(due)

    def _run_rotation(self, schedule_name: Optional[str] = None):
        """Internal method to run rotation with error handling"""
        try:
            if schedule_name == KIND_MONTHLY and datetime.now().day != 1:
                return

            logger.info(f"Scheduled rotation starting ({schedule_name or 'all jobs'})")
            if schedule_name is None:
                results = self.rotation_function()
            else:
                results = self._rotate_for(schedule_name)

            if not results:
                logger.info(f"Scheduled rotation '{schedule_name}': no jobs are due")
                return
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

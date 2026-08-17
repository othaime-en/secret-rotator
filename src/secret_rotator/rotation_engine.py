import threading
import time
from typing import Callable, Dict, List, Any, Optional
from secret_rotator.providers.base import SecretProvider
from secret_rotator.rotators.base import SecretRotator
from secret_rotator.utils.logger import logger
from secret_rotator.utils.retry import retry_with_backoff
from secret_rotator.backup_manager import BackupManager
from secret_rotator.config.settings import settings
from secret_rotator.audit_log import audit_log


class RotationInProgressError(Exception):
    """
    Raised by rotate_all_secrets() when a previous call is still
    running.

    RotationEngine only ever runs one full "rotate everything" sweep
    at a time, in-process, regardless of what triggered it — the web
    dashboard's manual "Rotate All" button, the background scheduler's
    periodic run, or a CLI --run-once invocation all go through this
    same guard. Before this, those paths had no coordination at all:
    a manual trigger firing while the scheduler's own run was
    mid-flight (or two manual triggers in quick succession) could
    race on the same jobs.

    This is an in-process lock only — it does not coordinate across
    multiple instances of the app running at once. That's tracked
    separately.
    """
    pass


class RotationEngine:
    """This is the main engine that orchestrates secret rotation"""

    def __init__(self):
        self.providers: Dict[str, SecretProvider] = {}
        self.rotators: Dict[str, SecretRotator] = {}
        self.rotation_jobs: List[Dict[str, Any]] = []
        self.backup_manager = BackupManager(
            backup_dir=settings.get("providers.file_storage.backup_path", "data/backup")
        )  # Use config or default
        # Guards "one full rotate_all_secrets() sweep at a time" —
        # see RotationInProgressError above for why.
        self._rotation_lock = threading.Lock()

    def register_provider(self, provider: SecretProvider):
        self.providers[provider.name] = provider
        logger.info(f"Registered provider: {provider.name}")

    def register_rotator(self, rotator: SecretRotator):
        self.rotators[rotator.name] = rotator
        logger.info(f"Registered rotator: {rotator.name}")

    def add_rotation_job(self, job_config: Dict[str, Any]):
        required_fields = ["name", "provider", "rotator", "secret_id"]
        for field in required_fields:
            if field not in job_config:
                logger.error(f"Missing required field '{field}' in job config")
                return False

        self.rotation_jobs.append(job_config)
        logger.info(f"Added rotation job: {job_config['name']}")
        return True

    @retry_with_backoff(
        max_attempts=settings.get("rotation.retry_attempts", 3), exceptions=(Exception,)
    )
    def rotate_secret(self, job_config: Dict[str, Any], actor: str = "system") -> bool:
        """Rotate a single secret based on job configuration.

        Args:
            job_config: the rotation job definition.
            actor: who triggered this — a dashboard username, or
                "system" for scheduler/CLI-triggered rotations. Recorded
                in the audit log (S5).
        """
        job_name = job_config["name"]
        provider_name = job_config["provider"]
        rotator_name = job_config["rotator"]
        secret_id = job_config["secret_id"]

        logger.info(f"Starting rotation for job: {job_name}")

        # Get provider and rotator
        provider = self.providers.get(provider_name)
        rotator = self.rotators.get(rotator_name)

        if not provider:
            logger.error(f"Provider '{provider_name}' not found")
            audit_log.log(
                "rotate", actor, secret_id=secret_id, success=False,
                details={"job": job_name, "reason": f"provider '{provider_name}' not found"},
            )
            return False

        if not rotator:
            logger.error(f"Rotator '{rotator_name}' not found")
            audit_log.log(
                "rotate", actor, secret_id=secret_id, success=False,
                details={"job": job_name, "reason": f"rotator '{rotator_name}' not found"},
            )
            return False

        try:
            # Step 1: Get current secret (for backup/rollback)
            current_secret = provider.get_secret(secret_id)
            logger.info(f"Retrieved current secret for {secret_id}")

            # Step 1.5: Create backup before rotation
            if settings.get("rotation.backup_old_secrets", True):
                try:
                    new_secret_temp = (
                        rotator.generate_new_secret()
                    )  # Generate early for backup metadata
                    backup_path = self.backup_manager.create_backup_with_checksum(
                        secret_id, current_secret, new_secret_temp
                    )
                    logger.info(f"Backup created at {backup_path}")
                except Exception as e:
                    logger.error(f"Backup failed for {job_name}, aborting rotation: {e}")
                    audit_log.log(
                        "rotate", actor, secret_id=secret_id, success=False,
                        details={"job": job_name, "reason": f"backup failed: {e}"},
                    )
                    return False

            # Step 2: Generate new secret (re-generate if needed, but we can reuse temp if backup succeeded)
            new_secret = (
                rotator.generate_new_secret()
                if "new_secret_temp" not in locals()
                else new_secret_temp
            )
            if not new_secret:
                logger.error(f"Failed to generate new secret for {job_name}")
                audit_log.log(
                    "rotate", actor, secret_id=secret_id, success=False,
                    details={"job": job_name, "reason": "secret generation failed"},
                )
                return False

            # Step 3: Validate new secret
            if not rotator.validate_secret(new_secret):
                logger.error(f"Generated secret failed validation for {job_name}")
                audit_log.log(
                    "rotate", actor, secret_id=secret_id, success=False,
                    details={"job": job_name, "reason": "generated secret failed validation"},
                )
                return False

            # Step 4: Update secret in provider
            success = provider.update_secret(secret_id, new_secret)
            if success:
                logger.info(f"Successfully rotated secret for {job_name}")
                audit_log.log(
                    "rotate", actor, secret_id=secret_id, success=True,
                    details={"job": job_name, "provider": provider_name, "rotator": rotator_name},
                )
                return True
            else:
                logger.error(f"Failed to update secret for {job_name}")
                audit_log.log(
                    "rotate", actor, secret_id=secret_id, success=False,
                    details={"job": job_name, "reason": "provider update_secret returned False"},
                )
                return False

        except Exception as e:
            logger.error(f"Error during rotation of {job_name}: {e}")
            audit_log.log(
                "rotate", actor, secret_id=secret_id, success=False,
                details={"job": job_name, "reason": str(e)},
            )
            return False

    def rotate_all_secrets(
        self,
        actor: str = "system",
        on_job_complete: Optional[Callable[[str, bool, int, int], None]] = None,
    ) -> Dict[str, bool]:
        """Rotate all configured secrets.

        Args:
            actor: who triggered this batch — passed through to each
                rotate_secret() call for the audit log (S5).
            on_job_complete: optional callback invoked after each
                individual job finishes, as
                on_job_complete(job_name, success, completed_count, total_count).
                Lets callers (namely the web API's background job
                tracker) report live progress instead of only learning
                the outcome once the entire sweep finishes. Exceptions
                raised by the callback are logged and otherwise
                ignored — a broken progress reporter should never fail
                the rotation itself.

        Raises:
            RotationInProgressError: if another call to this method
                (from any source — manual API trigger, the scheduler,
                a CLI run) is already in progress in this process.
        """
        if not self._rotation_lock.acquire(blocking=False):
            raise RotationInProgressError(
                "A full rotation sweep is already running in this process; "
                "try again once it finishes."
            )

        try:
            results = {}
            total = len(self.rotation_jobs)
            logger.info(f"Starting rotation of {total} secrets")

            for index, job in enumerate(self.rotation_jobs, start=1):
                job_name = job["name"]
                success = self.rotate_secret(job, actor=actor)
                results[job_name] = success

                if on_job_complete is not None:
                    try:
                        on_job_complete(job_name, success, index, total)
                    except Exception:
                        logger.error(
                            "on_job_complete callback raised an exception; "
                            "continuing rotation",
                            exc_info=True,
                        )

                # Add delay between rotations to avoid overwhelming systems
                time.sleep(1)

            successful = sum(1 for result in results.values() if result)
            logger.info(f"Rotation complete: {successful}/{len(results)} successful")

            return results
        finally:
            self._rotation_lock.release()
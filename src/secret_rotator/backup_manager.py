import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from secret_rotator.utils.logger import logger
from secret_rotator.encryption_manager import EncryptionManager, SecretMasker


class BackupManager:
    """Handle backup and recovery of secrets with encryption support"""

    def __init__(self, backup_dir: str = "data/backup", encrypt_backups: bool = True):
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.encrypt_backups = encrypt_backups

        # Initialize encryption manager if encryption is enabled
        self.encryption_manager = None
        if self.encrypt_backups:
            self.encryption_manager = EncryptionManager()
            logger.info("Backup encryption enabled")

    def create_backup(self, secret_id: str, old_value: str, new_value: str) -> str:
        """Create an encrypted backup of the old secret value"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_filename = f"{secret_id}_{timestamp}.json"
        backup_path = self.backup_dir / backup_filename

        # Prepare backup data
        backup_data = {
            "secret_id": secret_id,
            "timestamp": timestamp,
            "old_value": old_value,
            "new_value": new_value,
            "backup_created": datetime.now().isoformat(),
            "encrypted": self.encrypt_backups,
        }

        # Encrypt sensitive values if encryption is enabled
        if self.encrypt_backups and self.encryption_manager:
            try:
                backup_data["old_value"] = self.encryption_manager.encrypt(old_value)
                backup_data["new_value"] = self.encryption_manager.encrypt(new_value)
                logger.debug(f"Encrypted backup data for {secret_id}")
            except Exception as e:
                logger.error(f"Failed to encrypt backup for {secret_id}: {e}")
                raise

        try:
            with open(backup_path, "w") as f:
                json.dump(backup_data, f, indent=2)

            logger.info(f"Created backup for {secret_id}: {backup_path}")
            return str(backup_path)

        except Exception as e:
            logger.error(f"Failed to create backup for {secret_id}: {e}")
            raise

    def restore_backup(self, backup_file: str, decrypt: bool = True) -> Dict[str, Any]:
        """Load and optionally decrypt backup data for restoration"""
        backup_path = Path(backup_file)

        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_file}")

        try:
            with open(backup_path, "r") as f:
                backup_data = json.load(f)

            # Decrypt values if backup was encrypted and decryption is requested
            is_encrypted = backup_data.get("encrypted", False)
            if is_encrypted and decrypt and self.encryption_manager:
                try:
                    backup_data["old_value"] = self.encryption_manager.decrypt(
                        backup_data["old_value"]
                    )
                    backup_data["new_value"] = self.encryption_manager.decrypt(
                        backup_data["new_value"]
                    )
                    logger.debug(f"Decrypted backup data from {backup_file}")
                except Exception as e:
                    logger.error(f"Failed to decrypt backup {backup_file}: {e}")
                    raise

            logger.info(f"Loaded backup data from {backup_file}")
            return backup_data

        except Exception as e:
            logger.error(f"Failed to restore backup {backup_file}: {e}")
            raise

    def list_backups(self, secret_id: Optional[str] = None, mask_values: bool = True) -> list:
        """List available backups with masked secret values"""
        backups = []
        pattern = f"{secret_id}_*.json" if secret_id else "*.json"

        for backup_file in self.backup_dir.glob(pattern):
            try:
                with open(backup_file, "r") as f:
                    backup_data = json.load(f)
                    backup_data["backup_file"] = str(backup_file)

                    # Mask sensitive values in the listing
                    if mask_values:
                        is_encrypted = backup_data.get("encrypted", False)

                        if is_encrypted and self.encryption_manager:
                            # For encrypted backups, decrypt then mask
                            try:
                                old_decrypted = self.encryption_manager.decrypt(
                                    backup_data["old_value"]
                                )
                                new_decrypted = self.encryption_manager.decrypt(
                                    backup_data["new_value"]
                                )
                                backup_data["old_value_masked"] = (
                                    SecretMasker.mask_for_backup_display(old_decrypted)
                                )
                                backup_data["new_value_masked"] = (
                                    SecretMasker.mask_for_backup_display(new_decrypted)
                                )
                            except Exception as e:
                                logger.warning(f"Could not decrypt backup for masking: {e}")
                                backup_data["old_value_masked"] = "****"
                                backup_data["new_value_masked"] = "****"
                        else:
                            # For plaintext backups, just mask
                            backup_data["old_value_masked"] = SecretMasker.mask_for_backup_display(
                                backup_data["old_value"]
                            )
                            backup_data["new_value_masked"] = SecretMasker.mask_for_backup_display(
                                backup_data["new_value"]
                            )

                        # Remove actual values from listing for security
                        backup_data.pop("old_value", None)
                        backup_data.pop("new_value", None)

                    backups.append(backup_data)

            except Exception as e:
                logger.warning(f"Failed to read backup file {backup_file}: {e}")

        # Sort by timestamp (newest first)
        backups.sort(key=lambda x: x["timestamp"], reverse=True)
        return backups

    def cleanup_old_backups(self, days_to_keep: int = 30):
        """Remove backup files older than specified days"""
        cutoff_timestamp = datetime.now().timestamp() - (days_to_keep * 24 * 60 * 60)
        removed_count = 0

        for backup_file in self.backup_dir.glob("*.json"):
            try:
                file_timestamp = backup_file.stat().st_mtime
                if file_timestamp < cutoff_timestamp:
                    backup_file.unlink()
                    removed_count += 1
                    logger.info(f"Removed old backup: {backup_file}")

            except Exception as e:
                logger.warning(f"Failed to remove backup {backup_file}: {e}")

        logger.info(f"Cleanup complete: removed {removed_count} old backup files")
        return removed_count

    def verify_backup_integrity(self, backup_file: str) -> bool:
        """Verify that a backup can be successfully read and decrypted"""
        try:
            backup_data = self.restore_backup(backup_file, decrypt=True)

            # Check required fields
            required_fields = ["secret_id", "old_value", "new_value", "timestamp"]
            for field in required_fields:
                if field not in backup_data:
                    logger.error(f"Backup {backup_file} missing required field: {field}")
                    return False

            logger.info(f"Backup {backup_file} integrity verified")
            return True

        except Exception as e:
            logger.error(f"Backup integrity check failed for {backup_file}: {e}")
            return False

    def export_backup_metadata(self) -> Dict[str, Any]:
        """Export backup metadata for reporting (without secret values)"""
        all_backups = self.list_backups(mask_values=True)

        metadata = {
            "total_backups": len(all_backups),
            "encryption_enabled": self.encrypt_backups,
            "backup_directory": str(self.backup_dir),
            "secrets_with_backups": len(set(b["secret_id"] for b in all_backups)),
            "oldest_backup": all_backups[-1]["timestamp"] if all_backups else None,
            "newest_backup": all_backups[0]["timestamp"] if all_backups else None,
        }

        return metadata

    def create_backup_with_checksum(self, secret_id: str, old_value: str, new_value: str) -> str:
        """Create backup with checksum for integrity verification"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_filename = f"{secret_id}_{timestamp}.json"
        backup_path = self.backup_dir / backup_filename

        # Prepare backup data
        backup_data = {
            "secret_id": secret_id,
            "timestamp": timestamp,
            "old_value": old_value,
            "new_value": new_value,
            "backup_created": datetime.now().isoformat(),
            "encrypted": self.encrypt_backups,
        }

        # Encrypt if needed
        if self.encrypt_backups and self.encryption_manager:
            try:
                backup_data["old_value"] = self.encryption_manager.encrypt(old_value)
                backup_data["new_value"] = self.encryption_manager.encrypt(new_value)
            except Exception as e:
                logger.error(f"Failed to encrypt backup for {secret_id}: {e}")
                raise

        try:
            # Calculate checksum on the JSON string (without checksum field)
            backup_json = json.dumps(backup_data, indent=2, sort_keys=True)
            checksum = hashlib.sha256(backup_json.encode()).hexdigest()

            # Add checksum to data
            backup_data["checksum"] = checksum

            # Write backup with checksum
            with open(backup_path, "w") as f:
                json.dump(backup_data, f, indent=2)

            logger.info(f"Created backup with checksum for {secret_id}: {backup_path}")
            return str(backup_path)

        except Exception as e:
            logger.error(f"Failed to create backup for {secret_id}: {e}")
            raise

    def _calculate_backup_checksum(self, backup_path: Path) -> str:
        """Calculate SHA-256 checksum of backup file"""
        sha256 = hashlib.sha256()

        with open(backup_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)

        return sha256.hexdigest()

    def verify_backup_with_checksum(self, backup_file: str) -> Tuple[bool, str]:
        """
        Verify backup using stored checksum.
        Returns (is_valid, reason)
        """
        backup_path = Path(backup_file)

        if not backup_path.exists():
            return False, "file_not_found"

        try:
            with open(backup_path, "r") as f:
                backup_data = json.load(f)

            stored_checksum = backup_data.get("checksum")

            if not stored_checksum:
                # No checksum stored, fall back to full verification
                return self.verify_backup_integrity(backup_file), "no_checksum_stored"

            # Remove checksum for calculation
            temp_data = backup_data.copy()
            temp_data.pop("checksum", None)

            # Calculate checksum on the JSON string (same as creation)
            backup_json = json.dumps(temp_data, indent=2, sort_keys=True)
            calculated_checksum = hashlib.sha256(backup_json.encode()).hexdigest()

            if calculated_checksum == stored_checksum:
                return True, "checksum_valid"
            else:
                logger.error(
                    f"Checksum mismatch for {backup_file}: "
                    f"expected {stored_checksum}, got {calculated_checksum}"
                )
                return False, "checksum_mismatch"

        except Exception as e:
            logger.error(f"Error verifying backup checksum: {e}")
            return False, f"error: {str(e)}"


class BackupIntegrityChecker:
    """Verify backup integrity on a scheduled basis"""

    def __init__(self, backup_manager):
        self.backup_manager = backup_manager
        self.verification_log_file = Path("logs/backup_verification.log")
        self.verification_log_file.parent.mkdir(parents=True, exist_ok=True)

    def verify_all_backups(self) -> Dict[str, Any]:
        """
        Verify integrity of all backups.
        Returns a report with status of each backup.
        """
        logger.info("Starting scheduled backup integrity verification")

        report = {
            "timestamp": datetime.now().isoformat(),
            "total_backups": 0,
            "verified": 0,
            "failed": 0,
            "corrupted": [],
            "errors": [],
        }

        try:
            # Get all backups
            all_backups = self.backup_manager.list_backups(mask_values=False)
            report["total_backups"] = len(all_backups)

            for backup in all_backups:
                backup_file = backup["backup_file"]
                secret_id = backup["secret_id"]

                try:
                    # Verify this backup
                    is_valid = self.backup_manager.verify_backup_integrity(backup_file)

                    if is_valid:
                        report["verified"] += 1
                        logger.debug(f"Backup verified: {backup_file}")
                    else:
                        report["failed"] += 1
                        report["corrupted"].append(
                            {
                                "backup_file": backup_file,
                                "secret_id": secret_id,
                                "timestamp": backup.get("timestamp"),
                                "reason": "integrity_check_failed",
                            }
                        )
                        logger.error(f"Backup integrity check failed: {backup_file}")

                except Exception as e:
                    report["failed"] += 1
                    report["errors"].append(
                        {"backup_file": backup_file, "secret_id": secret_id, "error": str(e)}
                    )
                    logger.error(f"Error verifying backup {backup_file}: {e}")

            # Log the report
            self._log_verification_report(report)

            # Alert if there are failures
            if report["failed"] > 0:
                self._alert_verification_failures(report)

            logger.info(
                f"Backup verification complete: "
                f"{report['verified']}/{report['total_backups']} verified, "
                f"{report['failed']} failed"
            )

            return report

        except Exception as e:
            logger.error(f"Backup verification failed: {e}")
            report["errors"].append({"error": str(e), "context": "verification_process"})
            return report

    def _log_verification_report(self, report: Dict[str, Any]):
        """Log verification report to file"""
        try:
            with open(self.verification_log_file, "a") as f:
                f.write(json.dumps(report) + "\n")
        except Exception as e:
            logger.error(f"Failed to log verification report: {e}")

    def _alert_verification_failures(self, report: Dict[str, Any]):
        """Alert administrators about verification failures"""
        # This should integrate with our notification system
        logger.warning(
            f"ALERT: {report['failed']} backup(s) failed verification. "
            f"See {self.verification_log_file} for details."
        )

        # TODO: Send email/slack notification
        # if self.notification_manager:
        #     self.notification_manager.send_alert(
        #         subject="Backup Verification Failures",
        #         message=f"{report['failed']} backups failed verification",
        #         priority="high"
        #     )

    def verify_backup_checksums(self) -> Dict[str, Any]:
        """
        Verify backup checksums if they exist.
        More lightweight than full decryption verification.
        """
        report = {
            "timestamp": datetime.now().isoformat(),
            "backups_checked": 0,
            "checksum_matches": 0,
            "checksum_mismatches": 0,
            "no_checksum": 0,
            "errors": [],
        }

        all_backups = self.backup_manager.list_backups(mask_values=False)

        for backup in all_backups:
            backup_file = backup["backup_file"]
            report["backups_checked"] += 1

            try:
                # Use the backup_manager's verification method instead
                is_valid, reason = self.backup_manager.verify_backup_with_checksum(backup_file)

                if reason == "checksum_valid":
                    report["checksum_matches"] += 1
                elif reason == "checksum_mismatch":
                    report["checksum_mismatches"] += 1
                    logger.warning(f"Checksum mismatch for {backup_file}")
                elif reason == "no_checksum_stored":
                    report["no_checksum"] += 1
                    logger.info(f"No checksum stored for {backup_file}")
                else:
                    # Other errors (file not found, etc.)
                    report["errors"].append({"backup_file": backup_file, "error": reason})
                    logger.error(f"Error checking checksum for {backup_file}: {reason}")

            except Exception as e:
                report["errors"].append({"backup_file": backup_file, "error": str(e)})
                logger.error(f"Error checking checksum for {backup_file}: {e}")

        return report

    def _calculate_file_checksum(self, file_path: str) -> str:
        """Calculate SHA-256 checksum of file"""
        sha256 = hashlib.sha256()

        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)

        return sha256.hexdigest()

    def get_verification_history(self, days: int = 30) -> List[Dict[str, Any]]:
        """Get verification history for the last N days"""
        if not self.verification_log_file.exists():
            return []

        cutoff = datetime.now() - timedelta(days=days)
        history = []

        try:
            with open(self.verification_log_file, "r") as f:
                for line in f:
                    try:
                        report = json.loads(line)
                        report_time = datetime.fromisoformat(report["timestamp"])

                        if report_time > cutoff:
                            history.append(report)
                    except (json.JSONDecodeError, KeyError):
                        continue
        except Exception as e:
            logger.error(f"Error reading verification history: {e}")

        return sorted(history, key=lambda x: x["timestamp"], reverse=True)

    def get_backup_health_metrics(self) -> Dict[str, Any]:
        """Get overall backup health metrics"""
        recent_verifications = self.get_verification_history(days=7)

        if not recent_verifications:
            return {
                "status": "unknown",
                "message": "No recent verifications",
                "last_verification": None,
            }

        latest = recent_verifications[0]

        # Calculate success rate
        total_backups = latest.get("total_backups", 0)
        verified = latest.get("verified", 0)
        success_rate = (verified / total_backups * 100) if total_backups > 0 else 0

        # Determine health status
        if success_rate == 100:
            status = "healthy"
        elif success_rate >= 95:
            status = "warning"
        else:
            status = "critical"

        return {
            "status": status,
            "success_rate": round(success_rate, 2),
            "total_backups": total_backups,
            "verified": verified,
            "failed": latest.get("failed", 0),
            "last_verification": latest.get("timestamp"),
            "recent_corrupted": sum(len(v.get("corrupted", [])) for v in recent_verifications),
        }

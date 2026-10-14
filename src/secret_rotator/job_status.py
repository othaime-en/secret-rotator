"""
Per-job rotation status for the web dashboard.

There is deliberately no second "run history" store. Every rotation
attempt is already written to the audit log (see audit_log.py and
RotationEngine.rotate_secret) with the job name, outcome, actor and
failure reason, so the dashboard derives "last rotated", "last result"
and "last error" from those events. One source of truth means the
dashboard can never disagree with the audit trail.

Known limits, by design for now:

* ``next_rotation`` is the *global* schedule's next tick: the scheduler
  runs one schedule for every job, and does not read a per-job
  ``schedule`` field. It becomes per-job when per-job scheduling lands.
* ``rotating`` only reflects rotations running in this process (see
  RotationEngine.active_job_names).
"""

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from secret_rotator.audit_log import audit_log as default_audit_log
from secret_rotator.backup_manager import BackupIntegrityChecker
from secret_rotator.config.settings import settings
from secret_rotator.utils.logger import logger
from secret_rotator.utils.timeutil import parse_time, to_utc_iso

STATUS_ROTATING = "rotating"
STATUS_FAILED = "failed"
STATUS_CURRENT = "current"
STATUS_NEVER = "never_rotated"

# The audit log has no "give me everything" call, only "the last N hours".
# Ten years is "everything" for any realistic deployment.
_ALL_TIME_HOURS = 24 * 365 * 10

_ACTIVITY_ACTIONS = ("rotate", "restore")


class JobStatusService:
    """Builds the status data shown on the Overview and Jobs pages."""

    def __init__(self, engine, audit=None):
        self.engine = engine
        self.audit = audit if audit is not None else default_audit_log
        self._cache_lock = threading.Lock()
        self._cache_key = None
        self._cache_events: List[Dict[str, Any]] = []

    # ---- audit log access -------------------------------------------

    def _rotation_events(self) -> List[Dict[str, Any]]:
        """All rotate events, oldest first.

        The parsed result is cached until the audit file changes, so the
        dashboard polling this endpoint does not re-read the whole file
        on every request.
        """
        try:
            stat = self.audit.audit_file.stat()
            key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return []

        with self._cache_lock:
            if key != self._cache_key:
                events = self.audit.get_recent_events(hours=_ALL_TIME_HOURS, action="rotate")
                events.sort(
                    key=lambda e: parse_time(e.get("timestamp"))
                    or datetime.min.replace(tzinfo=timezone.utc)
                )
                self._cache_events = events
                self._cache_key = key
            return list(self._cache_events)

    @staticmethod
    def _latest_by_job(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for event in events:
            job = (event.get("details") or {}).get("job")
            if not job:
                continue
            record = latest.setdefault(job, {"last_attempt": None, "last_success": None})
            record["last_attempt"] = event
            if event.get("success"):
                record["last_success"] = event
        return latest

    # ---- jobs --------------------------------------------------------

    def _next_rotation(self) -> Optional[str]:
        scheduler = getattr(self.engine, "scheduler", None)
        if scheduler is None:
            return None
        next_time = scheduler.next_rotation_time()
        return next_time.isoformat() if next_time else None

    def job_summaries(self) -> List[Dict[str, Any]]:
        """One status record per configured job, in configuration order."""
        latest = self._latest_by_job(self._rotation_events())
        active = self.engine.active_job_names()
        next_rotation = self._next_rotation()
        default_schedule = settings.get("rotation.schedule", "daily")

        summaries = []
        for job in list(self.engine.rotation_jobs):
            name = job["name"]
            record = latest.get(name) or {}
            attempt = record.get("last_attempt")
            success = record.get("last_success")

            if name in active:
                status = STATUS_ROTATING
            elif attempt is None:
                status = STATUS_NEVER
            elif attempt.get("success"):
                status = STATUS_CURRENT
            else:
                status = STATUS_FAILED

            last_error = None
            if status == STATUS_FAILED:
                last_error = (attempt.get("details") or {}).get("reason")

            summaries.append(
                {
                    "name": name,
                    "secret_id": job.get("secret_id"),
                    "provider": job.get("provider"),
                    "rotator": job.get("rotator"),
                    "schedule": job.get("schedule") or default_schedule,
                    "status": status,
                    "last_rotated": success["timestamp"] if success else None,
                    "last_attempt": attempt["timestamp"] if attempt else None,
                    "last_error": last_error,
                    "next_rotation": next_rotation,
                }
            )
        return summaries

    def job_summary(self, name: str) -> Optional[Dict[str, Any]]:
        """Status record for one job, or None if no job has this name."""
        return next((j for j in self.job_summaries() if j["name"] == name), None)

    # ---- overview ----------------------------------------------------

    def _recent_activity(self, limit: int) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for action in _ACTIVITY_ACTIONS:
            events.extend(self.audit.get_recent_events(hours=24 * 30, action=action))
        events.sort(
            key=lambda e: parse_time(e.get("timestamp"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return [self._describe(e) for e in events[:limit]]

    @staticmethod
    def _describe(event: Dict[str, Any]) -> Dict[str, Any]:
        details = event.get("details") or {}
        success = bool(event.get("success"))
        action = event.get("action")
        subject = details.get("job") or event.get("secret_id") or "secret"

        if action == "rotate":
            message = f"{subject} rotated" if success else f"{subject} failed"
        elif action == "restore":
            message = f"{subject} restored from backup" if success else f"{subject} restore failed"
        else:
            message = f"{action} {subject}"

        return {
            "timestamp": event.get("timestamp"),
            "action": action,
            "success": success,
            "actor": event.get("actor"),
            "message": message,
        }

    def _verification(self) -> Dict[str, Any]:
        try:
            checker = BackupIntegrityChecker(self.engine.backup_manager)
            health = checker.get_backup_health_metrics()
            history = checker.get_verification_history(days=7)  # newest first
        except Exception as e:
            # Verification data is a nice-to-have on the Overview; never
            # let an unreadable log take the whole page down.
            logger.error(f"Could not read backup verification data: {e}")
            return {
                "status": "unknown",
                "last_verification": None,
                "total": 0,
                "verified": 0,
                "failed": 0,
                "recent_runs": [],
            }

        runs = [
            {"timestamp": to_utc_iso(run.get("timestamp")), "ok": run.get("failed", 0) == 0}
            for run in reversed(history[:7])
        ]
        return {
            "status": health.get("status", "unknown"),
            "last_verification": to_utc_iso(health.get("last_verification")),
            "total": health.get("total_backups", 0),
            "verified": health.get("verified", 0),
            "failed": health.get("failed", 0),
            "recent_runs": runs,
        }

    def overview(self, activity_limit: int = 4) -> Dict[str, Any]:
        jobs = self.job_summaries()

        attention = [
            {
                "kind": "job_failed",
                "job": job["name"],
                "detail": job["last_error"],
                "timestamp": job["last_attempt"],
            }
            for job in jobs
            if job["status"] == STATUS_FAILED
        ]

        return {
            "summary": {
                "jobs": len(jobs),
                "backups": self.engine.backup_manager.count_backups(),
                "rotating": sum(1 for job in jobs if job["status"] == STATUS_ROTATING),
            },
            "attention": attention,
            "jobs": jobs,
            "recent_activity": self._recent_activity(activity_limit),
            "verification": self._verification(),
        }

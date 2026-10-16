"""
Backup health data and manual verification for the web dashboard.

Verification reports are appended to ``logs/backup_verification.log`` by
BackupIntegrityChecker (on a schedule, or when triggered here). This
service reads that log to build the Backup health page's data, and runs
manual verifications in a background thread: verifying means decrypting
every backup, which can take long enough that doing it inside a request
would tie up a web server thread.

Known limit: manual-run state is kept in this process. With several web
instances behind a load balancer, ask the instance that started the run
(the finished report still lands in the shared log, so every instance
shows it afterwards).
"""

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from secret_rotator.audit_log import audit_log as default_audit_log
from secret_rotator.backup_manager import BackupIntegrityChecker
from secret_rotator.utils.logger import logger
from secret_rotator.utils.timeutil import parse_time, to_utc_iso

# The strip on the page always shows the last seven runs, whatever range
# the history table is set to.
RECENT_RUN_COUNT = 7
# Enough runs to fill the strip even when the table range is short.
_RECENT_LOOKBACK_DAYS = 365
MAX_FAILURES_LISTED = 50

RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"


def _success_rate(verified: int, total: int) -> Optional[float]:
    """Percentage of backups that verified, or None for an empty run."""
    if not total:
        return None
    return round(verified / total * 100, 1)


class BackupHealthService:
    """Builds the Backup health page data and runs manual verifications."""

    def __init__(self, engine, audit=None):
        self.engine = engine
        self.audit = audit if audit is not None else default_audit_log
        self._lock = threading.Lock()
        self._run: Optional[Dict[str, Any]] = None
        self._thread: Optional[threading.Thread] = None

    def _checker(self):
        scheduler = getattr(self.engine, "scheduler", None)
        checker = getattr(scheduler, "integrity_checker", None)
        return checker or BackupIntegrityChecker(self.engine.backup_manager)

    # ---- reading history -------------------------------------------------

    def _scrub(self, text: Any) -> str:
        """Remove the server's backup directory from error text."""
        result = str(text)
        backup_dir = self.engine.backup_manager.backup_dir
        for variant in {str(backup_dir), str(backup_dir.resolve())}:
            result = result.replace(variant + os.sep, "").replace(variant, "<backups>")
        return result

    @staticmethod
    def _file_name(path: Optional[str]) -> Optional[str]:
        return os.path.basename(path) if path else None

    def _failures(self, report: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
        failures: List[Dict[str, Any]] = []
        for item in report.get("corrupted", []):
            failures.append(
                {
                    "secret_id": item.get("secret_id"),
                    "file": self._file_name(item.get("backup_file")),
                    "reason": "Integrity check failed",
                }
            )
        for item in report.get("errors", []):
            failures.append(
                {
                    "secret_id": item.get("secret_id"),
                    "file": self._file_name(item.get("backup_file")),
                    "reason": self._scrub(item.get("error", "Unknown error")),
                }
            )
        return failures[:MAX_FAILURES_LISTED], len(failures)

    @staticmethod
    def _run_row(report: Dict[str, Any]) -> Dict[str, Any]:
        total = report.get("total_backups", 0)
        verified = report.get("verified", 0)
        return {
            "timestamp": to_utc_iso(report.get("timestamp")),
            "total": total,
            "verified": verified,
            "failed": report.get("failed", 0),
            "success_rate": _success_rate(verified, total),
        }

    def summary(self, days: int = 7) -> Dict[str, Any]:
        """Everything the Backup health page shows.

        Args:
            days: how far back the history table reaches.

        Returns:
            ``status`` (healthy / warning / critical / unknown), ``latest``
            (the newest run plus the backups that failed in it, or None),
            ``recent_runs`` (last seven runs, oldest first, each with an
            ``ok`` flag), ``history`` (runs within ``days``, newest first)
            and ``running`` (a manual verification is in progress).
        """
        checker = self._checker()
        recent = checker.get_verification_history(days=_RECENT_LOOKBACK_DAYS)  # newest first
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        history = [
            self._run_row(report)
            for report in recent
            if (parse_time(report.get("timestamp")) or cutoff) > cutoff
        ]

        latest = None
        status = "unknown"
        if recent:
            latest = self._run_row(recent[0])
            rate = latest["success_rate"]
            status = (
                BackupIntegrityChecker.classify_success_rate(rate)
                if rate is not None
                else "unknown"
            )
            failures, failures_total = self._failures(recent[0])
            latest["status"] = status
            latest["failures"] = failures
            latest["failures_total"] = failures_total

        recent_runs = [
            {"timestamp": to_utc_iso(r.get("timestamp")), "ok": r.get("failed", 0) == 0}
            for r in reversed(recent[:RECENT_RUN_COUNT])
        ]

        return {
            "status": status,
            "latest": latest,
            "recent_runs": recent_runs,
            "history": history,
            "days": days,
            "running": self._is_running(),
        }

    # ---- manual verification ---------------------------------------------

    def _is_running(self) -> bool:
        with self._lock:
            return self._run is not None and self._run["status"] == RUN_RUNNING

    @staticmethod
    def _public(run: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if run is None:
            return None
        copy = dict(run)
        if copy.get("report") is not None:
            copy["report"] = dict(copy["report"])
        return copy

    def start_verification(self, actor: str) -> Tuple[Dict[str, Any], bool]:
        """Start a verification of every backup in the background.

        Returns:
            ``(run_state, started)``. ``started`` is False when a
            verification is already running; ``run_state`` is then that
            run's state rather than a new one.
        """
        with self._lock:
            if self._run is not None and self._run["status"] == RUN_RUNNING:
                return self._public(self._run), False
            run = {
                "status": RUN_RUNNING,
                "actor": actor,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "report": None,
                "error": None,
            }
            self._run = run
            self._thread = threading.Thread(
                target=self._execute, args=(run, actor), daemon=True, name="backup-verification"
            )
            thread = self._thread
            snapshot = self._public(run)

        thread.start()
        return snapshot, True

    def _execute(self, run: Dict[str, Any], actor: str) -> None:
        status = RUN_FAILED
        report_summary = None
        error = None
        try:
            report = self._checker().verify_all_backups()
            report_summary = {
                "total": report.get("total_backups", 0),
                "verified": report.get("verified", 0),
                "failed": report.get("failed", 0),
            }
            process_errors = [
                e for e in report.get("errors", []) if e.get("context") == "verification_process"
            ]
            if process_errors:
                error = "Verification could not complete. See the application log."
            else:
                status = RUN_COMPLETED
        except Exception as e:
            logger.error(f"Manual backup verification failed: {e}", exc_info=True)
            error = "Verification failed. See the application log."

        success = status == RUN_COMPLETED and not (report_summary or {}).get("failed")
        details: Dict[str, Any] = dict(report_summary or {})
        if error:
            details["reason"] = error
        self.audit.log("verify_backups", actor, success=success, details=details)

        with self._lock:
            run["status"] = status
            run["report"] = report_summary
            run["error"] = error
            run["finished_at"] = datetime.now(timezone.utc).isoformat()

    def verification_status(self) -> Optional[Dict[str, Any]]:
        """State of the latest manual verification in this process, or
        None if none has run since the process started."""
        with self._lock:
            return self._public(self._run)

"""
Audit logging for secret rotation, restoration, and dashboard access.

"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from secret_rotator.config.settings import settings
from secret_rotator.utils.logger import logger


class AuditLog:
    """Append-only structured log of security-relevant events."""

    def __init__(self, audit_file: Optional[str] = None):
        self.audit_file = Path(
            audit_file or settings.get("audit.log_file", "logs/audit.log")
        )
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        action: str,
        actor: str,
        secret_id: Optional[str] = None,
        success: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record an audit event.

        Args:
            action: short event name, e.g. "rotate", "restore", "login",
                "logout", "login_failed", "verify_backups".
            actor: who did it — the logged-in dashboard username, or
                "system" for scheduler/CLI-triggered actions.
            secret_id: the secret affected, if applicable.
            success: whether the action succeeded.
            details: small, non-sensitive extra context (job name,
                provider, failure reason). Never put secret values here.
        """
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "actor": actor,
            "secret_id": secret_id,
            "success": success,
            "details": details or {},
        }

        try:
            with open(self.audit_file, "a") as f:
                f.write(json.dumps(event) + "\n")
        except OSError as e:
            # Never let an audit-log write failure break the actual
            # rotation/restore/login flow — but make sure it's loud in
            # the regular application log if it happens.
            logger.error(f"Failed to write audit event to {self.audit_file}: {e}")

        level = logger.info if success else logger.warning
        level(
            f"AUDIT action={action} actor={actor} secret_id={secret_id} "
            f"success={success}"
        )

    def get_recent_events(
        self,
        hours: int = 24,
        secret_id: Optional[str] = None,
        action: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Read back recent audit events, optionally filtered."""
        if not self.audit_file.exists():
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        events = []

        with open(self.audit_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    event_time = datetime.fromisoformat(event["timestamp"])
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

                if event_time < cutoff:
                    continue
                if secret_id is not None and event.get("secret_id") != secret_id:
                    continue
                if action is not None and event.get("action") != action:
                    continue

                events.append(event)

        return events


# Module-level singleton, matching the existing `settings`/`logger`
# convention in this codebase — import and use directly:
#   from secret_rotator.audit_log import audit_log
#   audit_log.log("rotate", actor="admin", secret_id="db_password")
audit_log = AuditLog()
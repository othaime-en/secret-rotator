"""
Activity log for the web dashboard, built on the audit log.

Why the audit log and not the application log: audit events are
structured (who did what to which secret, and whether it worked), they
are written by code that is careful never to include secret values, and
they are what an operator actually wants on an "Activity" page. The
application log is free text whose secret masking is best-effort, so it
is deliberately not exposed here.

Levels shown in the UI are derived, not stored:

* ``info``     - the action succeeded
* ``warning``  - a sign-in failed, or a backup verification found failed
                 backups
* ``error``    - a rotation, restore or verification itself failed

Known limit: the audit file is append-only and is read in full (and
cached until it changes), so a very long-lived deployment should rotate
or archive it. Nothing here deletes audit data.
"""

import csv
import io
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from secret_rotator.audit_log import audit_log as default_audit_log
from secret_rotator.utils.timeutil import to_utc_iso

LEVELS = ("info", "warning", "error")
ALL = "all"

MAX_PER_PAGE = 500
MAX_EXPORT_ROWS = 10_000
MAX_MESSAGE_LENGTH = 300

EXPORT_COLUMNS = ("timestamp", "level", "action", "actor", "secret_id", "success", "message")

# Spreadsheet programs run cells that start with these as formulas.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


_JOB_ACTIONS = {
    "job_create": "created",
    "job_update": "changed",
    "job_delete": "deleted",
    "job_pause": "paused",
    "job_resume": "resumed",
    "job_reset": "reset",
}


_COMPONENT_VERBS = {"create": "created", "update": "changed", "delete": "deleted", "reset": "reset"}


def _clip(text: Any) -> str:
    text = str(text)
    return text if len(text) <= MAX_MESSAGE_LENGTH else text[: MAX_MESSAGE_LENGTH - 1] + "…"


def _level(event: Dict[str, Any]) -> str:
    if event.get("success"):
        return "info"
    action = event.get("action")
    details = event.get("details") or {}
    if action in ("login", "login_failed"):
        return "warning"
    if action == "verify_backups" and details.get("failed") and not details.get("reason"):
        return "warning"
    return "error"


def _message(event: Dict[str, Any]) -> str:
    action = event.get("action")
    actor = event.get("actor") or "unknown"
    details = event.get("details") or {}
    success = bool(event.get("success"))
    subject = details.get("job") or event.get("secret_id") or "secret"
    reason = details.get("reason")
    suffix = f": {reason}" if reason else ""

    if action == "rotate":
        return f"{subject} rotated" if success else f"{subject} rotation failed{suffix}"
    if action == "restore":
        return f"{subject} restored from backup" if success else f"{subject} restore failed{suffix}"
    if action == "verify_backups":
        if "total" in details:
            summary = f"{details.get('verified', 0)} of {details['total']} backups verified"
            return f"Backup verification: {summary}{suffix}"
        return f"Backup verification {'finished' if success else 'failed'}{suffix}"
    if action == "login":
        return f"{actor} signed in" if success else f"Failed sign-in for {actor}"
    if action == "login_failed":
        return f"Failed sign-in for {actor}"
    if action == "logout":
        return f"{actor} signed out"
    for noun in ("provider", "rotator"):
        if action.startswith(noun + "_") and action[len(noun) + 1 :] in _COMPONENT_VERBS:
            verb = _COMPONENT_VERBS[action[len(noun) + 1 :]]
            return f"{actor} {verb} {noun} {details.get('name', '?')}"
    if action in _JOB_ACTIONS:
        return f"{actor} {_JOB_ACTIONS[action]} job {details.get('job', '?')}"
    if action == "export_activity":
        return f"{actor} exported the activity log ({details.get('format', '?')})"
    return f"{action} {event.get('secret_id') or ''}".strip()


class ActivityService:
    """Reads, filters and exports audit events for the Activity page."""

    def __init__(self, audit=None):
        self.audit = audit if audit is not None else default_audit_log
        self._lock = threading.Lock()
        self._cache_key = None
        self._cache: List[Dict[str, Any]] = []

    # ---- reading -----------------------------------------------------------

    @staticmethod
    def _enrich(event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "seq": event["seq"],
            "timestamp": to_utc_iso(event.get("timestamp")),
            "level": _level(event),
            "action": event.get("action"),
            "actor": event.get("actor"),
            "secret_id": event.get("secret_id"),
            "success": bool(event.get("success")),
            "message": _clip(_message(event)),
        }

    def _events(self) -> List[Dict[str, Any]]:
        """All events, oldest first. Parsed again only when the file changes."""
        try:
            stat = self.audit.audit_file.stat()
            key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return []

        with self._lock:
            if key != self._cache_key:
                self._cache = [self._enrich(e) for e in self.audit.read_events()]
                self._cache_key = key
            return self._cache

    @staticmethod
    def _validate_level(level: str) -> None:
        if level != ALL and level not in LEVELS:
            raise ValueError(f"level must be one of: {ALL}, {', '.join(LEVELS)}")

    def _filtered(self, level: str, query: str) -> List[Dict[str, Any]]:
        """Matching events, oldest first."""
        self._validate_level(level)
        needle = (query or "").strip().lower()
        matches = []
        for event in self._events():
            if level != ALL and event["level"] != level:
                continue
            if needle:
                haystack = (
                    f"{event['action']} {event['actor']} {event['secret_id']} {event['message']}"
                )
                if needle not in haystack.lower():
                    continue
            matches.append(event)
        return matches

    def latest_seq(self) -> int:
        events = self._events()
        return events[-1]["seq"] if events else 0

    def query(
        self, level: str = ALL, q: str = "", page: int = 1, per_page: int = 100
    ) -> Dict[str, Any]:
        """One page of matching events, newest first.

        Raises:
            ValueError: ``level`` is not all/info/warning/error.
        """
        per_page = max(1, min(int(per_page), MAX_PER_PAGE))
        matches = self._filtered(level, q)
        matches.reverse()

        total = len(matches)
        pages = max(1, -(-total // per_page))
        page = min(max(1, int(page)), pages)
        window = matches[(page - 1) * per_page : page * per_page]
        return {
            "events": window,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
            "latest_seq": self.latest_seq(),
        }

    def since(self, after: int, level: str = ALL, q: str = "", limit: int = 200) -> Dict[str, Any]:
        """Matching events newer than ``after`` (a ``seq``), newest first.

        Used by the live view to fetch only what is new. ``latest_seq`` is
        the newest event overall (matching or not), so the next poll can
        start from it. If it is *lower* than ``after`` the audit file was
        rotated or truncated and the caller should reload.
        """
        limit = max(1, min(int(limit), MAX_PER_PAGE))
        fresh = [e for e in self._filtered(level, q) if e["seq"] > after]
        fresh.reverse()
        return {"events": fresh[:limit], "latest_seq": self.latest_seq()}

    # ---- export ------------------------------------------------------------

    def export_rows(self, level: str = ALL, q: str = "") -> Tuple[List[Dict[str, Any]], bool]:
        """Matching events, oldest first, capped to the newest
        MAX_EXPORT_ROWS. Returns ``(rows, truncated)``."""
        matches = self._filtered(level, q)
        truncated = len(matches) > MAX_EXPORT_ROWS
        return (matches[-MAX_EXPORT_ROWS:] if truncated else matches), truncated

    @staticmethod
    def _csv_cell(value: Any) -> str:
        text = "" if value is None else str(value)
        return "'" + text if text.startswith(_FORMULA_PREFIXES) else text

    def to_csv(self, rows: List[Dict[str, Any]]) -> str:
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(EXPORT_COLUMNS)
        for row in rows:
            writer.writerow([self._csv_cell(row.get(column)) for column in EXPORT_COLUMNS])
        return out.getvalue()

    @staticmethod
    def to_json(rows: List[Dict[str, Any]]) -> str:
        trimmed = [{column: row.get(column) for column in EXPORT_COLUMNS} for row in rows]
        return json.dumps(trimmed, indent=2)

    def record_export(self, actor: str, fmt: str, rows: int) -> None:
        """Audit-log that someone exported the log."""
        self.audit.log("export_activity", actor, details={"format": fmt, "rows": rows})

    @staticmethod
    def export_filename(fmt: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"activity-{stamp}.{fmt}"

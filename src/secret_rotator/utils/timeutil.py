"""Small helpers for turning the project's mixed timestamp styles into
timezone-aware UTC.

The audit log writes timezone-aware UTC ISO strings, while backups and
the verification log write naive local-time ISO strings
(``datetime.now().isoformat()``). The dashboard needs one unambiguous
form, so everything it sends to the browser goes through here.
"""

from datetime import datetime, timezone
from typing import Optional


def parse_time(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp to an aware UTC datetime, or None.

    Naive timestamps are interpreted as local time.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc)


def to_utc_iso(value: Optional[str]) -> Optional[str]:
    """Like parse_time(), but returns an ISO 8601 string (or None)."""
    parsed = parse_time(value)
    return parsed.isoformat() if parsed else None

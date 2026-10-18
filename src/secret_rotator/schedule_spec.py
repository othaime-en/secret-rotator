"""
Rotation schedule values: what is valid, and when "monthly" next fires.

A schedule is configured as a short string, globally (``rotation.schedule``)
and optionally per job (the job's ``schedule`` key):

    daily              every day at 02:00 (local time)
    weekly             every 7 days, counted from when the scheduler started
    monthly            the 1st of each month at 02:00 (local time)
    every_30_minutes   every N minutes (N >= 1)
    every_2_hours      every N hours (N >= 1)

Anything else is an error. Before this module an unrecognised value was
silently ignored and nothing was ever scheduled; "monthly", which the
example config suggested, was one such value.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Offered by the dashboard's job editor. Any valid every_N_minutes /
# every_N_hours value is also accepted.
SCHEDULE_CHOICES = ("every_30_minutes", "every_2_hours", "daily", "weekly", "monthly")

DAILY_TIME = "02:00"  # also used for "monthly" (on the 1st)
_DAILY_HOUR, _DAILY_MINUTE = 2, 0

KIND_DAILY = "daily"
KIND_WEEKLY = "weekly"
KIND_MONTHLY = "monthly"
KIND_INTERVAL = "interval"

_INTERVAL = re.compile(r"^every_(\d+)_(minute|minutes|hour|hours)$")


@dataclass(frozen=True)
class ScheduleSpec:
    """A parsed schedule. ``name`` is the canonical spelling and is what
    jobs are grouped by (``every_1_hour`` and ``every_1_hours`` are the
    same schedule)."""

    name: str
    kind: str
    interval: int = 0
    unit: str = ""  # "minutes" or "hours" for interval schedules


def parse_schedule(value: Any) -> ScheduleSpec:
    """Parse a schedule string.

    Raises:
        ValueError: with a message that lists the accepted forms.
    """
    text = value.strip().lower() if isinstance(value, str) else ""

    if text in (KIND_DAILY, KIND_WEEKLY, KIND_MONTHLY):
        return ScheduleSpec(name=text, kind=text)

    match = _INTERVAL.match(text)
    if match:
        interval = int(match.group(1))
        if interval >= 1:
            unit = "minutes" if match.group(2).startswith("minute") else "hours"
            return ScheduleSpec(
                name=f"every_{interval}_{unit}", kind=KIND_INTERVAL, interval=interval, unit=unit
            )

    raise ValueError(
        f"invalid schedule {value!r}: use daily, weekly, monthly, "
        "every_<N>_minutes or every_<N>_hours (N must be at least 1)"
    )


def is_valid_schedule(value: Any) -> bool:
    try:
        parse_schedule(value)
    except ValueError:
        return False
    return True


def next_monthly(now: datetime) -> datetime:
    """The next 1st of a month at 02:00 strictly after ``now``.

    Naive and local, like the ``schedule`` library's own times.
    """
    this_month = now.replace(day=1, hour=_DAILY_HOUR, minute=_DAILY_MINUTE, second=0, microsecond=0)
    if this_month > now:
        return this_month
    if now.month == 12:
        return this_month.replace(year=now.year + 1, month=1)
    return this_month.replace(month=now.month + 1)

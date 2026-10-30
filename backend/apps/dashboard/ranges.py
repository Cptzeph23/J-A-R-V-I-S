"""Resolve a dashboard range (today, week, month, custom) in a timezone (SRS-DSH-01, FR-DSH-002).

Days are calendar days in the requested zone. A range is stored as inclusive local dates plus
the matching half-open UTC interval [start, end), computed from local midnights, so a daylight
saving day that is 23 or 25 hours long is measured correctly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_RANGE_DAYS = 366
PRESETS = ("today", "week", "month", "custom")
_ZONE_NAME = re.compile(r"[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+){0,2}")


class RangeError(ValueError):
    """Raised with a message that is safe to show to the caller."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True, slots=True)
class DashboardRange:
    preset: str
    first: date  # inclusive
    last: date  # inclusive
    zone: ZoneInfo
    start_utc: datetime  # inclusive
    end_utc: datetime  # exclusive

    @property
    def days(self) -> list[date]:
        return [self.first + timedelta(days=i) for i in range((self.last - self.first).days + 1)]

    def to_dict(self) -> dict[str, str]:
        start_local = self.start_utc.astimezone(self.zone)
        # "to" is the last instant of the last day, as in docs/06 (23:59:59 local).
        end_local = (self.end_utc - timedelta(seconds=1)).astimezone(self.zone)
        return {
            "preset": self.preset,
            "from": start_local.isoformat(),
            "to": end_local.isoformat(),
            "from_date": self.first.isoformat(),
            "to_date": self.last.isoformat(),
            "timezone": self.zone.key,
        }


def parse_zone(name: str | None, default: str) -> ZoneInfo:
    candidate = (name or "").strip() or default
    if len(candidate) > 64 or not _ZONE_NAME.fullmatch(candidate):
        raise RangeError("tz", "Unknown timezone.")
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise RangeError("tz", "Unknown timezone.") from exc


def _parse_date(field: str, raw: str | None) -> date:
    if not raw:
        raise RangeError(field, "Required for a custom range.")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise RangeError(field, "Must be YYYY-MM-DD.") from exc


def _local_midnight_utc(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)


def resolve_range(
    preset: str | None,
    *,
    date_from: str | None,
    date_to: str | None,
    zone: ZoneInfo,
    now: datetime,
) -> DashboardRange:
    chosen = (preset or "today").strip().lower()
    if chosen not in PRESETS:
        raise RangeError("range", f"Use one of: {', '.join(PRESETS)}.")
    today = now.astimezone(zone).date()
    if chosen == "today":
        first = last = today
    elif chosen == "week":  # Monday to Sunday, the calendar week containing today
        first = today - timedelta(days=today.weekday())
        last = first + timedelta(days=6)
    elif chosen == "month":
        first = today.replace(day=1)
        next_month = (first + timedelta(days=32)).replace(day=1)
        last = next_month - timedelta(days=1)
    else:
        first, last = _parse_date("from", date_from), _parse_date("to", date_to)
        if first > last:
            raise RangeError("from", "Must not be after `to`.")
        if (last - first).days + 1 > MAX_RANGE_DAYS:
            raise RangeError("to", f"At most {MAX_RANGE_DAYS} days.")
    return DashboardRange(
        preset=chosen,
        first=first,
        last=last,
        zone=zone,
        start_utc=_local_midnight_utc(first, zone),
        end_utc=_local_midnight_utc(last + timedelta(days=1), zone),
    )

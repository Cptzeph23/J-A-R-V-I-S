"""Reading the calendar: ranges, conflicts and day/week summaries, all in the owner's zone."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.db.models import Q, QuerySet

from apps.accounts.models import User
from apps.common.timezones import iso_utc
from apps.integrations.models import Integration, IntegrationStatus, Kind

from .. import conflicts, store
from ..models import CalendarEvent

WORKDAY_START = time(9, 0)
WORKDAY_END = time(17, 0)
MIN_FREE_MINUTES = 30


def owner_zone(owner: User) -> ZoneInfo:
    return store.resolve_zone(owner.timezone, "UTC")


def calendar_integration(owner: User) -> Integration | None:
    return (
        Integration.objects.for_owner(owner)
        .filter(kind=Kind.CALENDAR)
        .exclude(status=IntegrationStatus.REVOKED)
        .first()
    )


def unavailable_reason(integration: Integration | None) -> str | None:
    """Why calendar data cannot be offered, or None when it can."""
    if integration is None:
        return "calendar_not_connected"
    if integration.status == IntegrationStatus.REAUTH_REQUIRED:
        return "reconnect_needed"
    if integration.last_synced_at is None:
        return "first_sync_pending"
    return None


def local_day_bounds(zone: ZoneInfo, day: date) -> tuple[datetime, datetime]:
    """UTC instants of this local day's start and the next day's start (23 to 25 hours long
    across a clock change)."""
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    return start, end


def events_between(owner: User, start: datetime, end: datetime) -> QuerySet[CalendarEvent]:
    """Occurrences overlapping [start, end); a zero-length event counts when it starts inside."""
    return CalendarEvent.objects.for_owner(owner).filter(
        Q(starts_at__lt=end) & (Q(ends_at__gt=start) | Q(starts_at__gte=start))
    )


def to_span(row: CalendarEvent) -> conflicts.Span:
    return conflicts.Span(str(row.pk), row.starts_at, row.ends_at, row.blocks_time)


def event_dict(row: CalendarEvent) -> dict[str, Any]:
    return {
        "id": str(row.pk),
        "title": store.title_of(row),
        "location": store.location_of(row),
        "starts_at": iso_utc(row.starts_at),
        "ends_at": iso_utc(row.ends_at),
        "all_day": row.all_day,
        "timezone": row.timezone,
        "status": row.status,
        "blocks_time": row.blocks_time,
        "self_response": row.self_response,
        "is_organizer": row.is_organizer,
        "attendee_count": row.attendee_count,
        "repeating": bool(row.recurring_event_id),
        "version": row.etag,
    }


def find_conflicts(
    owner: User, start: datetime, end: datetime, *, exclude_id: str = ""
) -> list[CalendarEvent]:
    """Stored events a proposed [start, end) would collide with."""
    if end <= start:
        return []
    rows = list(events_between(owner, start, end).filter(blocks_time=True))
    hit_ids = {s.key for s in conflicts.conflicts_with(map(to_span, rows), start, end)}
    return [r for r in rows if str(r.pk) in hit_ids and str(r.pk) != exclude_id]


def _range(zone: ZoneInfo, scope: str, anchor: date) -> tuple[date, date]:
    if scope == "week":
        first = anchor - timedelta(days=anchor.weekday())
        return first, first + timedelta(days=6)
    return anchor, anchor


def summary(owner: User, *, scope: str, anchor: date, now: datetime) -> dict[str, Any]:
    zone = owner_zone(owner)
    first, last = _range(zone, scope, anchor)
    integration = calendar_integration(owner)
    base: dict[str, Any] = {
        "scope": scope,
        "from": first.isoformat(),
        "to": last.isoformat(),
        "timezone": zone.key,
        "source": "google_calendar",
    }
    reason = unavailable_reason(integration)
    if integration is None or integration.last_synced_at is None or reason is not None:
        return {**base, "available": False, "reason": reason, "data_as_of": None}

    start, _ = local_day_bounds(zone, first)
    _, end = local_day_bounds(zone, last)
    rows = list(events_between(owner, start, end))
    timed = [r for r in rows if not r.all_day]
    spans = [to_span(r) for r in rows]
    by_id = {str(r.pk): r for r in rows}

    days: list[dict[str, Any]] = []
    free: list[dict[str, Any]] = []
    day = first
    while day <= last:
        d_start, d_end = local_day_bounds(zone, day)
        day_rows = [r for r in rows if conflicts.overlaps(r.starts_at, r.ends_at, d_start, d_end)]
        days.append(
            {
                "date": day.isoformat(),
                "event_count": len([r for r in day_rows if not r.all_day]),
                "all_day_count": len([r for r in day_rows if r.all_day]),
                "busy_minutes": conflicts.busy_minutes(spans, d_start, d_end),
            }
        )
        if scope == "day" or day.weekday() < 5:
            w_start = datetime.combine(day, WORKDAY_START, tzinfo=zone).astimezone(UTC)
            w_end = datetime.combine(day, WORKDAY_END, tzinfo=zone).astimezone(UTC)
            free.extend(
                {
                    "starts_at": iso_utc(a),
                    "ends_at": iso_utc(b),
                    "minutes": int((b - a).total_seconds() // 60),
                }
                for a, b in conflicts.free_windows(
                    spans,
                    max(w_start, now) if w_start < now < w_end else w_start,
                    w_end,
                    min_minutes=MIN_FREE_MINUTES,
                )
                if b > now
            )
        day += timedelta(days=1)

    upcoming = next((r for r in timed if r.ends_at > now), None)
    return {
        **base,
        "available": True,
        "reason": None,
        "data_as_of": iso_utc(integration.last_synced_at),
        "degraded": integration.status != IntegrationStatus.CONNECTED,
        "event_count": len(timed),
        "all_day_count": len(rows) - len(timed),
        "busy_minutes": conflicts.busy_minutes(spans, start, end),
        "next_event": event_dict(upcoming) if upcoming else None,
        "days": days,
        "events": [event_dict(r) for r in rows],
        "conflicts": [
            {
                "first": event_dict(by_id[a.key]),
                "second": event_dict(by_id[b.key]),
                "overlap_minutes": int(
                    (min(a.ends_at, b.ends_at) - max(a.starts_at, b.starts_at)).total_seconds()
                    // 60
                ),
            }
            for a, b in conflicts.conflicting_pairs(spans)
        ],
        "free_windows": free,
        "assumptions": [
            "All-day, free-marked and declined events do not count as busy or as conflicts.",
            "Free windows are 09:00 to 17:00 local time, weekdays only for a week view, "
            f"at least {MIN_FREE_MINUTES} minutes long.",
            "Synced events cover the last 14 days and the next 90 days only.",
        ],
    }

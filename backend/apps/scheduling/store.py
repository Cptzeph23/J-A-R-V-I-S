"""Turning provider events into rows, and back into readable values."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apps.common import secretbox
from apps.integrations.models import Integration
from apps.integrations.providers.base import CalendarEventData

from .models import CalendarEvent

TITLE_MAX = 300
LOCATION_MAX = 300


def resolve_zone(name: str, fallback: str) -> ZoneInfo:
    for candidate in (name, fallback):
        if candidate:
            try:
                return ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError, OSError):
                continue
    return ZoneInfo("UTC")


def _instant(value: datetime | date, zone: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.combine(value, time.min, tzinfo=zone).astimezone(UTC)


def row_values(data: CalendarEventData, owner_timezone: str) -> dict[str, object]:
    """Column values for one occurrence. All-day dates become local midnights in the event's
    zone (the owner's when the provider names none), so ordering and overlap work uniformly."""
    zone = resolve_zone(data.timezone, owner_timezone)
    all_day = not isinstance(data.start, datetime)
    starts = _instant(data.start, zone)
    ends = max(_instant(data.end, zone), starts)
    return {
        "recurring_event_id": data.recurring_event_id,
        "starts_at": starts,
        "ends_at": ends,
        "all_day": all_day,
        "timezone": zone.key,
        "status": "tentative" if data.status == "tentative" else "confirmed",
        "blocks_time": not all_day and not data.transparent and data.self_response != "declined",
        "self_response": data.self_response,
        "is_organizer": data.is_organizer,
        "attendee_count": data.attendee_count,
        "has_conference": data.has_conference,
        "etag": data.etag,
        "provider_updated_at": data.updated_at,
    }


def upsert(integration: Integration, data: CalendarEventData, owner_timezone: str) -> CalendarEvent:
    existing = CalendarEvent.objects.filter(
        integration=integration, provider_event_id=data.id
    ).first()
    pk = existing.pk if existing else uuid.uuid4()
    values = row_values(data, owner_timezone)
    values["title_sealed"] = secretbox.seal(data.title[:TITLE_MAX], f"calendar.title:{pk}")
    values["location_sealed"] = (
        secretbox.seal(data.location[:LOCATION_MAX], f"calendar.location:{pk}")
        if data.location
        else ""
    )
    if existing is None:
        return CalendarEvent.objects.create(
            id=pk,
            owner_id=integration.owner_id,
            integration=integration,
            provider_event_id=data.id,
            **values,
        )
    for name, value in values.items():
        setattr(existing, name, value)
    existing.save()
    return existing


def title_of(row: CalendarEvent) -> str:
    try:
        return secretbox.open_sealed(row.title_sealed, f"calendar.title:{row.pk}")
    except secretbox.SecretBoxError:
        return ""


def location_of(row: CalendarEvent) -> str:
    if not row.location_sealed:
        return ""
    try:
        return secretbox.open_sealed(row.location_sealed, f"calendar.location:{row.pk}")
    except secretbox.SecretBoxError:
        return ""

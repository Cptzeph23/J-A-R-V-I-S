"""Create, change and cancel events (the only write path to the calendar).

Rules that hold for every call:
- Nothing is ever sent to other people: calls pass sendUpdates=none, a created event has no
  attendees, and an event that has other attendees is never changed or cancelled from here.
- Retrying is safe. A create uses an event id derived from the idempotency key, so the second
  attempt finds the first one's event instead of making another (scenario 12). An update that
  is already applied, and a cancel of something already gone, succeed without doing it twice.
- Changes are made against the version the owner approved: if the event changed since, the call
  fails with `event_changed` and nothing is written.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from apps.accounts.models import User
from apps.audit.recorder import record
from apps.integrations.models import Integration, IntegrationStatus, Kind
from apps.integrations.providers.base import (
    CalendarEventData,
    EventAlreadyExists,
    EventChanged,
    EventNotFound,
    EventSpec,
    ProviderError,
)
from apps.integrations.providers.registry import get_calendar_provider
from apps.integrations.services.credentials import access_token_for
from apps.integrations.services.errors import IntegrationUnusableError

from .. import store
from ..errors import CalendarError, unavailable
from ..models import CalendarEvent


@dataclass(frozen=True, slots=True)
class WriteResult:
    event: CalendarEvent | None
    outcome: Literal[
        "created", "already_created", "updated", "already_applied", "cancelled", "already_gone"
    ]


def event_id_for(owner_id: uuid.UUID, idempotency_key: str) -> str:
    """A valid Google event id (lowercase hex is inside its base32hex alphabet), stable per
    owner and key."""
    digest = hashlib.sha256(f"jarvis.calendar.create:{owner_id}:{idempotency_key}".encode())
    return digest.hexdigest()[:32]


def _integration(owner: User) -> Integration:
    found = (
        Integration.objects.for_owner(owner)
        .filter(
            kind=Kind.CALENDAR,
            status__in=[IntegrationStatus.CONNECTED, IntegrationStatus.DEGRADED],
        )
        .first()
    )
    if found is None:
        raise unavailable()
    return found


def _token(integration: Integration) -> str:
    try:
        return access_token_for(integration)
    except IntegrationUnusableError:
        raise unavailable() from None


def _provider_failure() -> CalendarError:
    return CalendarError(
        "calendar_unavailable", "The calendar provider could not be reached. Try again."
    )


def create_event(owner: User, *, spec: EventSpec, idempotency_key: str) -> WriteResult:
    integration = _integration(owner)
    token = _token(integration)
    provider = get_calendar_provider(integration.provider)
    event_id = event_id_for(owner.pk, idempotency_key)
    outcome: Literal["created", "already_created"] = "created"
    try:
        data: CalendarEventData = provider.insert_event(token, event_id, spec)
    except EventAlreadyExists:
        outcome = "already_created"
        try:
            existing = provider.get_event(token, event_id)
        except ProviderError:
            raise _provider_failure() from None
        if existing is None:
            raise CalendarError(
                "event_deleted", "An earlier attempt created this event and it was since deleted."
            ) from None
        data = existing
    except ProviderError:
        raise _provider_failure() from None
    row = store.upsert(integration, data, owner.timezone)
    if outcome == "created":
        record(
            "calendar.event_created",
            owner_id=owner.pk,
            target_type="calendar_event",
            target_id=row.pk,
        )
    return WriteResult(row, outcome)


def _owned_row(owner: User, local_id: uuid.UUID) -> CalendarEvent:
    row = (
        CalendarEvent.objects.for_owner(owner)
        .select_related("integration")
        .filter(pk=local_id)
        .first()
    )
    if row is None:
        raise CalendarError("not_found", "No such event.")
    if row.attendee_count > 0 or not row.is_organizer:
        raise CalendarError(
            "not_allowed",
            "This event involves other people, so it can only be changed in your calendar app.",
        )
    return row


def _same(current: CalendarEventData, spec: EventSpec) -> bool:
    def instant(value: datetime | object) -> datetime | None:
        return value.astimezone(UTC) if isinstance(value, datetime) else None

    checks = [
        spec.title is None or current.title == spec.title,
        spec.location is None or current.location == spec.location,
        spec.start is None or instant(current.start) == spec.start.astimezone(UTC),
        spec.end is None or instant(current.end) == spec.end.astimezone(UTC),
    ]
    return all(checks)


def update_event(
    owner: User,
    *,
    local_id: uuid.UUID,
    version: str,
    title: str | None = None,
    location: str | None = None,
    description: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    timezone: str | None = None,
) -> WriteResult:
    row = _owned_row(owner, local_id)
    if row.all_day:
        raise CalendarError(
            "not_allowed", "All-day events can only be changed in your calendar app."
        )
    new_start = start or row.starts_at
    new_end = end or (new_start + (row.ends_at - row.starts_at) if start else row.ends_at)
    if new_end <= new_start:
        raise CalendarError("invalid_time", "The event must end after it starts.")
    spec = EventSpec(
        title=title,
        location=location,
        description=description,
        start=new_start if (start or end) else None,
        end=new_end if (start or end) else None,
        timezone=(timezone or row.timezone) if (start or end) else timezone,
    )
    integration = row.integration
    token = _token(integration)
    provider = get_calendar_provider(integration.provider)
    try:
        current = provider.get_event(token, row.provider_event_id)
        if current is None:
            row.delete()
            raise CalendarError("not_found", "That event no longer exists.")
        if _same(current, spec):
            return WriteResult(
                store.upsert(integration, current, owner.timezone), "already_applied"
            )
        if current.etag != version:
            store.upsert(integration, current, owner.timezone)
            raise CalendarError(
                "event_changed", "The event changed since it was read. Read it again."
            )
        updated = provider.patch_event(token, row.provider_event_id, spec, etag=current.etag)
    except EventChanged:
        raise CalendarError(
            "event_changed", "The event changed since it was read. Read it again."
        ) from None
    except EventNotFound:
        row.delete()
        raise CalendarError("not_found", "That event no longer exists.") from None
    except ProviderError:
        raise _provider_failure() from None
    saved = store.upsert(integration, updated, owner.timezone)
    record(
        "calendar.event_updated",
        owner_id=owner.pk,
        target_type="calendar_event",
        target_id=saved.pk,
    )
    return WriteResult(saved, "updated")


def cancel_event(owner: User, *, local_id: uuid.UUID, version: str) -> WriteResult:
    row = _owned_row(owner, local_id)
    integration = row.integration
    token = _token(integration)
    provider = get_calendar_provider(integration.provider)
    row_id = row.pk
    try:
        current = provider.get_event(token, row.provider_event_id)
        if current is None:
            row.delete()
            return WriteResult(None, "already_gone")
        if current.etag != version:
            store.upsert(integration, current, owner.timezone)
            raise CalendarError(
                "event_changed", "The event changed since it was read. Read it again."
            )
        provider.delete_event(token, row.provider_event_id, etag=current.etag)
    except EventNotFound:
        row.delete()
        return WriteResult(None, "already_gone")
    except EventChanged:
        raise CalendarError(
            "event_changed", "The event changed since it was read. Read it again."
        ) from None
    except ProviderError:
        raise _provider_failure() from None
    row.delete()
    record(
        "calendar.event_cancelled",
        owner_id=owner.pk,
        target_type="calendar_event",
        target_id=row_id,
    )
    return WriteResult(None, "cancelled")

"""Recording events once and turning each into at most one notification."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User
from apps.notifications.services import planning

from ..models import Event, EventStatus
from ..rules import assess

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IngestResult:
    event: Event
    created: bool


def ingest(
    owner_id: uuid.UUID,
    source: str,
    external_event_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> IngestResult:
    """Record an event. A repeat of (owner, source, external id) is counted and dropped."""
    try:
        with transaction.atomic():
            event = Event.objects.create(
                owner_id=owner_id,
                source=source,
                external_event_id=external_event_id[:200],
                type=event_type,
                payload=payload or {},
            )
    except IntegrityError:
        Event.objects.filter(
            owner_id=owner_id, source=source, external_event_id=external_event_id[:200]
        ).update(duplicate_count=F("duplicate_count") + 1)
        existing = Event.objects.get(
            owner_id=owner_id, source=source, external_event_id=external_event_id[:200]
        )
        return IngestResult(existing, False)
    _enqueue_process(event.pk)
    return IngestResult(event, True)


def safe_ingest(
    owner_id: uuid.UUID,
    source: str,
    external_event_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """For callers whose own work must never fail because a notification could not be recorded."""
    try:
        ingest(owner_id, source, external_event_id, event_type, payload)
    except Exception:
        logger.warning("event_ingest_failed", event_type=event_type)


def _enqueue_process(event_id: uuid.UUID) -> None:
    from ..tasks import process_event_task

    transaction.on_commit(lambda: process_event_task.delay(str(event_id)))


def process(event_id: uuid.UUID, now: datetime | None = None) -> str:
    """Assess one event and plan its notification. Safe to run twice: the second run sees the
    event already processed, and a crash before commit leaves it new for the next run."""
    moment = now or timezone.now()
    with transaction.atomic():
        event = Event.objects.select_for_update().filter(pk=event_id).first()
        if event is None:
            return "missing"
        if event.status == EventStatus.PROCESSED:
            return "already_processed"
        verdict = assess(event.type, event.payload)
        if verdict.notify:
            owner = User.objects.get(pk=event.owner_id)
            event.notification = planning.create_for_event(
                owner,
                event_type=event.type,
                payload=event.payload,
                assessment=verdict,
                now=moment,
            )
        event.importance, event.reason = verdict.importance, verdict.reason
        event.status, event.processed_at = EventStatus.PROCESSED, moment
        event.save(
            update_fields=[
                "importance",
                "reason",
                "status",
                "processed_at",
                "notification",
                "updated_at",
            ]
        )
    return "processed"

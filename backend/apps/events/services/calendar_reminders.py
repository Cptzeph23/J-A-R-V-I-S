"""Events for calendar items that are about to start."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from apps.integrations.models import IntegrationStatus
from apps.scheduling.models import CalendarEvent

from ..models import Source
from .ingest import safe_ingest

ACTIVE = [IntegrationStatus.CONNECTED, IntegrationStatus.DEGRADED]


def scan(now: datetime | None = None) -> int:
    """One event per upcoming occurrence (and per new start time if it is moved)."""
    moment = now or timezone.now()
    horizon = moment + timedelta(minutes=settings.CALENDAR_REMINDER_MINUTES)
    upcoming = CalendarEvent.objects.filter(
        starts_at__gt=moment,
        starts_at__lte=horizon,
        blocks_time=True,
        all_day=False,
        integration__status__in=ACTIVE,
    ).order_by("starts_at")[:500]
    count = 0
    for row in upcoming:
        minutes = max(1, math.ceil((row.starts_at - moment).total_seconds() / 60))
        safe_ingest(
            row.owner_id,
            Source.CALENDAR,
            f"soon:{row.pk}:{int(row.starts_at.timestamp())}",
            "calendar.starting_soon",
            {"event_id": str(row.pk), "minutes": minutes},
        )
        count += 1
    return count

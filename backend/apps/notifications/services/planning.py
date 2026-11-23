"""Turning an assessed event into a notification, holding and merging as the owner asked."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.models import User
from apps.events.rules import Assessment, render

from .. import planner
from ..models import (
    PRIORITY_ORDER,
    Notification,
    NotificationPreference,
    NotificationSettings,
    NotificationState,
)


def owner_zone(owner: User) -> ZoneInfo:
    try:
        return ZoneInfo(owner.timezone)
    except (KeyError, ValueError, OSError):
        return ZoneInfo("UTC")


def preference_for(owner_id: uuid.UUID, category: str) -> planner.Preference:
    row = NotificationPreference.objects.for_owner(owner_id).filter(category=category).first()
    return planner.Preference(row.enabled, row.min_priority) if row else planner.Preference()


def quiet_hours_for(owner_id: uuid.UUID) -> planner.QuietHours:
    row = NotificationSettings.objects.for_owner(owner_id).first()
    if row is None:
        return planner.QuietHours()
    return planner.QuietHours(row.quiet_start, row.quiet_end, row.critical_overrides_quiet)


def _last_sent(owner_id: uuid.UUID, group_key: str) -> datetime | None:
    value = (
        Notification.objects.for_owner(owner_id)
        .filter(group_key=group_key, state=NotificationState.DISPATCHED)
        .aggregate(latest=Max("dispatched_at"))["latest"]
    )
    return value if isinstance(value, datetime) else None


def _decide(
    owner: User, category: str, priority: str, group_key: str, now: datetime
) -> planner.Plan:
    return planner.plan(
        priority=priority,
        preference=preference_for(owner.pk, category),
        quiet=quiet_hours_for(owner.pk),
        now=now,
        zone=owner_zone(owner),
        last_sent_in_group=_last_sent(owner.pk, group_key),
        group_window=timedelta(seconds=settings.NOTIFICATION_GROUP_WINDOW_SECONDS),
    )


def create_for_event(
    owner: User,
    *,
    event_type: str,
    payload: dict[str, Any],
    assessment: Assessment,
    now: datetime | None = None,
) -> Notification:
    """Create, merge into a held notification, or suppress. Caller holds the transaction."""
    moment = now or timezone.now()
    verdict = _decide(
        owner, assessment.category, assessment.importance, assessment.group_key, moment
    )
    text = render(event_type, 1, payload)
    common: dict[str, Any] = {
        "owner_id": owner.pk,
        "category": assessment.category,
        "priority": assessment.importance,
        "title": text.title,
        "body": text.body,
        "data": text.data,
        "group_key": assessment.group_key,
    }
    if verdict.action == "suppress":
        return Notification.objects.create(
            **common, state=NotificationState.SUPPRESSED, reason=verdict.reason
        )
    if verdict.action == "hold":
        held = (
            Notification.objects.for_owner(owner)
            .select_for_update()
            .filter(group_key=assessment.group_key, state=NotificationState.HELD)
            .first()
        )
        if held is not None:
            return _merge(held, event_type, payload, assessment.importance)
        return Notification.objects.create(
            **common,
            state=NotificationState.HELD,
            reason=verdict.reason,
            scheduled_for=verdict.until,
        )
    notification = Notification.objects.create(**common, state=NotificationState.READY)
    _enqueue_dispatch(notification.pk)
    return notification


def _merge(
    held: Notification, event_type: str, payload: dict[str, Any], priority: str
) -> Notification:
    held.group_count += 1
    text = render(event_type, held.group_count, payload)
    held.title, held.body = text.title, text.body
    if PRIORITY_ORDER[priority] > PRIORITY_ORDER[held.priority]:
        held.priority = priority
    held.save(update_fields=["group_count", "title", "body", "priority", "updated_at"])
    return held


def release_due(now: datetime | None = None) -> int:
    """Re-plan held notifications whose time has come. Returns how many were released."""
    moment = now or timezone.now()
    released = 0
    ids = list(
        Notification.objects.filter(state=NotificationState.HELD, scheduled_for__lte=moment)
        .order_by("scheduled_for")
        .values_list("pk", flat=True)[:200]
    )
    for pk in ids:
        with transaction.atomic():
            row = (
                Notification.objects.select_for_update(skip_locked=True)
                .filter(pk=pk, state=NotificationState.HELD)
                .first()
            )
            if row is None:
                continue
            owner = User.objects.get(pk=row.owner_id)
            verdict = _decide(owner, row.category, row.priority, row.group_key, moment)
            if verdict.action == "send":
                row.state, row.scheduled_for = NotificationState.READY, None
                released += 1
                _enqueue_dispatch(row.pk)
            elif verdict.action == "hold":
                row.scheduled_for, row.reason = verdict.until, verdict.reason
            else:
                row.state, row.reason = NotificationState.SUPPRESSED, verdict.reason
            row.save(update_fields=["state", "scheduled_for", "reason", "updated_at"])
    return released


def _enqueue_dispatch(notification_id: uuid.UUID) -> None:
    from ..tasks import dispatch_task

    transaction.on_commit(lambda: dispatch_task.delay(str(notification_id)))

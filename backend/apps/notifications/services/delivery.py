"""Fan a ready notification out to the owner's devices and send each delivery with retries.

At-least-once with a floor: a delivery that reached the provider is never sent again (its status
is `sent`), a failed one is retried with backoff and jitter until it succeeds or gives up, and
the notification's id is the collapse key so the rare send that succeeded but was not recorded
(a crash between the two) replaces itself on the device instead of showing twice.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.models import AuditActor
from apps.audit.recorder import record
from apps.common import secretbox
from apps.devices.models import Device
from apps.devices.services.push import token_context

from ..models import (
    PRIORITY_ORDER,
    DeliveryStatus,
    Notification,
    NotificationDelivery,
    NotificationState,
)
from ..providers import get_push_provider
from ..providers.base import (
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderUnavailable,
    PushMessage,
    PushRejected,
    PushTokenInvalid,
)

MAX_BACKOFF_SECONDS = 3600
RETRYABLE = (DeliveryStatus.QUEUED, DeliveryStatus.FAILED_RETRY)


def backoff_seconds(attempts: int, jitter_unit: float, retry_after: int = 0) -> int:
    """30s, 60s, 120s ... capped, scaled by jitter in [0.5, 1.0]; honours Retry-After."""
    base = min(30 * (2 ** max(attempts - 1, 0)), MAX_BACKOFF_SECONDS)
    return max(retry_after, int(base * (0.5 + 0.5 * jitter_unit)))


def _jitter() -> float:
    return secrets.randbelow(10_001) / 10_000


@dataclass(frozen=True, slots=True)
class FanOut:
    created: int
    delivery_ids: list[uuid.UUID]


def fan_out(notification_id: uuid.UUID, now: datetime | None = None) -> FanOut:
    """READY -> DISPATCHED with one delivery per device holding a push token. Idempotent."""
    moment = now or timezone.now()
    with transaction.atomic():
        row = (
            Notification.objects.select_for_update()
            .filter(pk=notification_id, state=NotificationState.READY)
            .first()
        )
        if row is None:
            return FanOut(0, [])
        devices = Device.objects.for_owner(row.owner_id).filter(
            revoked_at__isnull=True,
            push_token_hash__gt="",  # nosec B106
        )
        created = 0
        for device in devices:
            _, was_created = NotificationDelivery.objects.get_or_create(
                notification=row,
                device=device,
                defaults={"owner_id": row.owner_id, "provider": device.push_provider},
            )
            created += int(was_created)
        row.state, row.dispatched_at = NotificationState.DISPATCHED, moment
        row.save(update_fields=["state", "dispatched_at", "updated_at"])
        ids = list(
            NotificationDelivery.objects.filter(notification=row, status__in=RETRYABLE).values_list(
                "pk", flat=True
            )
        )
    return FanOut(created, ids)


def due_delivery_ids(now: datetime, limit: int = 200) -> list[uuid.UUID]:
    return list(
        NotificationDelivery.objects.filter(status__in=RETRYABLE)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lt=now))
        .filter(notification__state=NotificationState.DISPATCHED)
        .order_by("created_at")
        .values_list("pk", flat=True)[:limit]
    )


def _claim(delivery_id: uuid.UUID, now: datetime) -> NotificationDelivery | None:
    lease = now + timedelta(seconds=settings.NOTIFICATION_LEASE_SECONDS)
    claimed = (
        NotificationDelivery.objects.filter(pk=delivery_id, status__in=RETRYABLE)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lt=now))
        .update(lease_until=lease)
    )
    if not claimed:
        return None
    return NotificationDelivery.objects.select_related("notification", "device").get(pk=delivery_id)


def send_delivery(delivery_id: uuid.UUID, now: datetime | None = None) -> str:
    """One attempt. Returns the resulting status name, or "skipped" when not claimable."""
    moment = now or timezone.now()
    delivery = _claim(delivery_id, moment)
    if delivery is None:
        return "skipped"
    attempts = delivery.attempts + 1
    device = delivery.device
    provider = get_push_provider(device.push_provider) if device.push_provider else None
    if device.revoked_at is not None or provider is None or not device.push_token_sealed:
        return _finish(delivery, DeliveryStatus.FAILED_PERMANENT, attempts, "no_push_token")
    try:
        token = secretbox.open_sealed(device.push_token_sealed, token_context(device.pk))
    except secretbox.SecretBoxError:
        return _finish(delivery, DeliveryStatus.FAILED_PERMANENT, attempts, "token_unreadable")

    note = delivery.notification
    message = PushMessage(
        title=note.title,
        body=note.body,
        data={**{k: str(v) for k, v in note.data.items()}, "notification_id": str(note.pk)},
        collapse_key=str(note.pk),
        high_priority=PRIORITY_ORDER[note.priority] >= PRIORITY_ORDER["high"],
    )
    try:
        provider.send(token, message)
    except PushTokenInvalid:
        _forget_token(device)
        return _finish(delivery, DeliveryStatus.FAILED_PERMANENT, attempts, "token_invalid")
    except ProviderNotConfigured:
        return _finish(
            delivery, DeliveryStatus.FAILED_PERMANENT, attempts, "provider_not_configured"
        )
    except PushRejected:
        return _finish(delivery, DeliveryStatus.FAILED_PERMANENT, attempts, "rejected")
    except ProviderUnavailable as exc:
        retry_after = exc.retry_after_seconds if isinstance(exc, ProviderRateLimited) else 0
        return _retry_later(delivery, attempts, exc.code, moment, retry_after)
    except Exception as exc:  # a provider bug must not lose the delivery or loop the worker
        return _retry_later(delivery, attempts, type(exc).__name__[:40].lower(), moment, 0)
    return _finish(delivery, DeliveryStatus.SENT, attempts, "", sent_at=moment)


def _retry_later(
    delivery: NotificationDelivery, attempts: int, error: str, now: datetime, retry_after: int
) -> str:
    if attempts >= settings.NOTIFICATION_MAX_ATTEMPTS:
        return _finish(delivery, DeliveryStatus.FAILED_PERMANENT, attempts, "gave_up")
    wait = backoff_seconds(attempts, _jitter(), retry_after)
    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        status=DeliveryStatus.FAILED_RETRY,
        attempts=attempts,
        last_error=error[:60],
        next_attempt_at=now + timedelta(seconds=wait),
        lease_until=None,
    )
    return DeliveryStatus.FAILED_RETRY.value


def _finish(
    delivery: NotificationDelivery,
    status: DeliveryStatus,
    attempts: int,
    error: str,
    *,
    sent_at: datetime | None = None,
) -> str:
    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        status=status,
        attempts=attempts,
        last_error=error,
        sent_at=sent_at,
        next_attempt_at=None,
        lease_until=None,
    )
    return status.value


def _forget_token(device: Device) -> None:
    Device.objects.filter(pk=device.pk).update(
        push_provider="",
        push_token_sealed="",
        push_token_hash="",
        push_registered_at=None,  # nosec B106
    )
    record(
        "device.push_token_dropped",
        owner_id=device.owner_id,
        actor=AuditActor.SYSTEM,
        target_type="device",
        target_id=device.pk,
        detail={"reason": "provider_reported_invalid"},
    )


def acknowledge(notification: Notification, device_id: uuid.UUID | None, now: datetime) -> None:
    """The owner opened it. Marks it read once and the receiving device's delivery as opened."""
    with transaction.atomic():
        if notification.read_at is None:
            notification.read_at = now
            notification.save(update_fields=["read_at", "updated_at"])
        if device_id is not None:
            NotificationDelivery.objects.filter(
                notification=notification, device_id=device_id, status=DeliveryStatus.SENT
            ).update(status=DeliveryStatus.DELIVERED_ACK, acked_at=now)

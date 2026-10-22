"""Device-session operations that do not depend on how the caller authenticated."""

from __future__ import annotations

import uuid
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.audit.recorder import record

from ..models import Device, RefreshToken


def revoke_session(device: Device, *, now: datetime | None = None) -> bool:
    """Revoke a device and every refresh token it holds. Idempotent.

    Returns True if this call changed anything. Access tokens stop working on the next
    request because authentication checks the device row. Callers are responsible for
    auditing; this function never raises on an already-revoked device.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        changed = False
        if device.revoked_at is None:
            device.revoked_at = moment
            device.save(update_fields=["revoked_at", "updated_at"])
            changed = True
        revoked = RefreshToken.objects.filter(device=device, revoked_at__isnull=True).update(
            revoked_at=moment
        )
    return changed or revoked > 0


def revoke_device(
    device: Device, *, actor_device_id: uuid.UUID | None = None, ip: str | None = None
) -> bool:
    """Owner-initiated revocation (DELETE /devices/{id}); audited."""
    with transaction.atomic():
        changed = revoke_session(device)
        if changed:
            record(
                "device.revoked",
                owner_id=device.owner_id,
                device_id=actor_device_id,
                ip=ip,
                target_type="device",
                target_id=device.id,
                detail={"platform": device.platform, "name": device.name},
            )
    return changed


def rename_device(device: Device, *, name: str, ip: str | None = None) -> Device:
    with transaction.atomic():
        old = device.name
        device.name = name
        device.save(update_fields=["name", "updated_at"])
        record(
            "device.renamed",
            owner_id=device.owner_id,
            ip=ip,
            target_type="device",
            target_id=device.id,
            detail={"old": old, "new": name},
        )
    return device

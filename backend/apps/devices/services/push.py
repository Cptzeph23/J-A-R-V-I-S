"""Push registration for a device."""

from __future__ import annotations

import hashlib
import uuid

from django.db import transaction
from django.utils import timezone

from apps.audit.recorder import record
from apps.common import secretbox

from ..models import Device

PROVIDERS = ("fcm", "apns")


def token_context(device_id: uuid.UUID) -> str:
    return f"device.push_token:{device_id}"


def register(device: Device, *, provider: str, token: str, ip: str | None = None) -> Device:
    """Store the token sealed. A token belongs to one device: if another device row held it
    (the app was reinstalled or the user signed in again), that row loses it."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        Device.objects.filter(push_token_hash=digest).exclude(pk=device.pk).update(
            push_provider="",
            push_token_sealed="",
            push_token_hash="",
            push_registered_at=None,  # nosec B106
        )
        device.push_provider = provider
        device.push_token_sealed = secretbox.seal(token, token_context(device.pk))
        device.push_token_hash = digest
        device.push_registered_at = timezone.now()
        device.save(
            update_fields=[
                "push_provider",
                "push_token_sealed",
                "push_token_hash",
                "push_registered_at",
                "updated_at",
            ]
        )
        record(
            "device.push_token_registered",
            owner_id=device.owner_id,
            ip=ip,
            target_type="device",
            target_id=device.pk,
            detail={"provider": provider},
        )
    return device


def clear(device: Device, *, ip: str | None = None) -> bool:
    if not device.push_token_hash:
        return False
    with transaction.atomic():
        device.push_provider = ""
        device.push_token_sealed = ""  # nosec B105
        device.push_token_hash = ""  # nosec B105
        device.push_registered_at = None
        device.save(
            update_fields=[
                "push_provider",
                "push_token_sealed",
                "push_token_hash",
                "push_registered_at",
                "updated_at",
            ]
        )
        record(
            "device.push_token_removed",
            owner_id=device.owner_id,
            ip=ip,
            target_type="device",
            target_id=device.pk,
        )
    return True

"""Collection settings and the gate every upload passes through (SRS-PHN-02)."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit.recorder import record
from apps.devices.models import Device, DevicePlatform

from ..errors import CollectionOffError, CollectionPausedError, CollectionUnsupportedError
from ..models import CollectionSettings


def get_settings(device: Device) -> CollectionSettings:
    row, _ = CollectionSettings.objects.get_or_create(
        device=device, defaults={"owner_id": device.owner_id}
    )
    return row


def update_settings(
    device: Device,
    *,
    enabled: bool | None = None,
    paused: bool | None = None,
    retention_days: int | None = None,
) -> CollectionSettings:
    with transaction.atomic():
        row = CollectionSettings.objects.select_for_update().filter(device=device).first()
        if row is None:
            row = CollectionSettings(device=device, owner_id=device.owner_id)
        before = (row.enabled, row.paused, row.retention_days)
        if enabled is not None:
            row.enabled = enabled
            if not enabled:
                row.paused_at = None  # off is off; "paused" only means something while on
        if paused is not None and row.enabled:
            row.paused_at = (row.paused_at or timezone.now()) if paused else None
        if retention_days is not None:
            row.retention_days = retention_days
        row.save()
        after = (row.enabled, row.paused, row.retention_days)
        if before != after:
            record(
                "phone.collection_settings_changed",
                owner_id=device.owner_id,
                device_id=device.pk,
                target_type="device",
                target_id=device.pk,
                detail={
                    "enabled": row.enabled,
                    "paused": row.paused,
                    "retention_days": row.retention_days,
                },
            )
    return row


def require_collecting(device: Device) -> CollectionSettings:
    """Raise unless this device may upload right now."""
    if device.platform != DevicePlatform.ANDROID:
        raise CollectionUnsupportedError()
    row = CollectionSettings.objects.filter(device=device).first()
    if row is None or not row.enabled:
        raise CollectionOffError()
    if row.paused:
        raise CollectionPausedError()
    return row


def touch_upload(row: CollectionSettings) -> None:
    CollectionSettings.objects.filter(pk=row.pk).update(last_upload_at=timezone.now())

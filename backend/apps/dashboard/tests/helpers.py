"""Seeding helpers shared by the dashboard tests. Ground truth is always written by hand."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from apps.accounts.models import User
from apps.devices.models import Device
from apps.phonelog.models import CallRecord, CollectionSettings, MessageRecord

_COUNTER = iter(range(1, 1_000_000))


def utc(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def enable_collection(
    device: Device, *, uploaded_at: datetime | None = None, paused: bool = False
) -> CollectionSettings:
    return CollectionSettings.objects.create(
        owner_id=device.owner_id,
        device=device,
        enabled=True,
        paused_at=utc(2026, 10, 20) if paused else None,
        last_upload_at=uploaded_at or utc(2026, 10, 27, 9, 0),
    )


def add_call(
    owner: User,
    device: Device,
    direction: str,
    started_at: datetime,
    duration: int = 0,
) -> CallRecord:
    return CallRecord.objects.create(
        owner=owner,
        device=device,
        source_record_id=f"c{next(_COUNTER)}",
        direction=direction,
        started_at=started_at,
        duration_seconds=duration,
    )


def add_message(owner: User, device: Device, direction: str, occurred_at: datetime) -> Any:
    return MessageRecord.objects.create(
        owner=owner,
        device=device,
        source_record_id=f"m{next(_COUNTER)}",
        direction=direction,
        occurred_at=occurred_at,
        length_chars=40,
    )

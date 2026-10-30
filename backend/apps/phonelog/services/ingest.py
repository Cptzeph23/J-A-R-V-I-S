"""Batch ingest of call and message metadata from the Android collector (SRS-PHN-01).

Idempotency has two layers: the `Idempotency-Key` header replays an identical request, and the
unique (device, source_record_id) constraint makes re-sent records harmless even when the
request itself differs (a retry that carries extra records, or a collector re-sync).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from apps.devices.models import Device

from ..models import CallRecord, MessageRecord
from .collection import require_collecting, touch_upload
from .hashing import counterpart_hash


@dataclass(slots=True)
class BatchResult:
    received: int = 0
    created: int = 0
    duplicates: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)


def _dedupe(records: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in records:
        key = item["source_record_id"]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique, len(records) - len(unique)


def ingest_calls(device: Device, records: Sequence[dict[str, Any]]) -> BatchResult:
    settings_row = require_collecting(device)
    unique, in_batch = _dedupe(records)
    rows = [
        CallRecord(
            owner_id=device.owner_id,
            device=device,
            source_record_id=r["source_record_id"],
            direction=r["direction"],
            started_at=r["started_at"],
            duration_seconds=r["duration_seconds"],
            counterpart_hash=counterpart_hash(device.owner_id, r.get("counterpart", "")),
        )
        for r in unique
    ]
    with transaction.atomic():
        CallRecord.objects.bulk_create(rows, ignore_conflicts=True)
        # Primary keys are generated client-side, so the rows that really landed are exactly
        # those whose pk now exists; the rest were already stored.
        created = CallRecord.objects.filter(pk__in=[r.pk for r in rows]).count()
        touch_upload(settings_row)
    return BatchResult(
        received=len(records), created=created, duplicates=in_batch + len(rows) - created
    )


def ingest_messages(device: Device, records: Sequence[dict[str, Any]]) -> BatchResult:
    settings_row = require_collecting(device)
    unique, in_batch = _dedupe(records)
    rows = [
        MessageRecord(
            owner_id=device.owner_id,
            device=device,
            source_record_id=r["source_record_id"],
            direction=r["direction"],
            occurred_at=r["occurred_at"],
            length_chars=r["length_chars"],
            counterpart_hash=counterpart_hash(device.owner_id, r.get("counterpart", "")),
        )
        for r in unique
    ]
    with transaction.atomic():
        MessageRecord.objects.bulk_create(rows, ignore_conflicts=True)
        created = MessageRecord.objects.filter(pk__in=[r.pk for r in rows]).count()
        touch_upload(settings_row)
    return BatchResult(
        received=len(records), created=created, duplicates=in_batch + len(rows) - created
    )

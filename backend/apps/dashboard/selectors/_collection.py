"""Shared view of the owner's phone-collection state, used by the calls and messages cards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, Max, Q

from apps.accounts.models import User
from apps.phonelog.models import CollectionSettings


@dataclass(frozen=True, slots=True)
class CollectionState:
    ever_uploaded: bool
    collecting_now: bool
    any_enabled: bool
    last_upload_at: datetime | None

    def unavailable_reason(self) -> str | None:
        """None when the card has real data behind it; otherwise why it does not."""
        if self.ever_uploaded:
            return None
        return "no_data_received_yet" if self.any_enabled else "collection_not_enabled"


def collection_state(owner: User) -> CollectionState:
    agg = CollectionSettings.objects.for_owner(owner).aggregate(
        # Aliases must not collide with model field names (`enabled`, `last_upload_at`).
        n_enabled=Count("id", filter=Q(enabled=True)),
        n_collecting=Count("id", filter=Q(enabled=True, paused_at__isnull=True)),
        latest_upload=Max("last_upload_at"),
    )
    return CollectionState(
        ever_uploaded=agg["latest_upload"] is not None,
        collecting_now=agg["n_collecting"] > 0,
        any_enabled=agg["n_enabled"] > 0,
        last_upload_at=agg["latest_upload"],
    )


def unavailable_card(
    source: str, reason: str, data_as_of: datetime | None = None
) -> dict[str, object]:
    return {"available": False, "reason": reason, "source": source, "data_as_of": data_as_of}

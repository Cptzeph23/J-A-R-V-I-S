"""Read-side queries for finance. Every function takes the owner and scopes by it."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from django.db.models import Max

from apps.accounts.models import User

from ..models import Transaction, TransactionQuerySet


def list_transactions(
    owner: User,
    *,
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    merchant_id: uuid.UUID | None = None,
    kind: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    uncategorized: bool = False,
) -> TransactionQuerySet:
    queryset = Transaction.objects.live_for_owner(owner).select_related(
        "account", "category", "merchant"
    )
    filters: dict[str, Any] = {}
    if account_id:
        filters["account_id"] = account_id
    if category_id:
        filters["category_id"] = category_id
    if merchant_id:
        filters["merchant_id"] = merchant_id
    if kind:
        filters["kind"] = kind
    if date_from:
        filters["posted_on__gte"] = date_from
    if date_to:
        filters["posted_on__lte"] = date_to
    queryset = queryset.filter(**filters)
    if uncategorized:
        queryset = queryset.filter(category__isnull=True)
    return queryset


def latest_data_timestamp(owner: User) -> Any:
    return Transaction.objects.live_for_owner(owner).aggregate(latest=Max("created_at"))["latest"]

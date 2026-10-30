"""Invalidate an owner's cached dashboard cards when their inputs change.

Covers single-row writes made through the ORM. Bulk paths (CSV import, retention and purge)
call `datastamp.touch` themselves because `bulk_create` and fast deletes send no signals.
"""

from __future__ import annotations

from typing import Any

from django.db.models.signals import post_delete, post_save

from apps.common.datastamp import touch
from apps.finance.models import AccountingPolicy, Category, FinancialAccount, Transaction
from apps.phonelog.models import CollectionSettings

WATCHED = (Transaction, Category, FinancialAccount, AccountingPolicy, CollectionSettings)


def _on_change(sender: Any, instance: Any, **kwargs: Any) -> None:
    owner_id = getattr(instance, "owner_id", None)
    if owner_id is not None:
        touch(owner_id)


def connect() -> None:
    for model in WATCHED:
        uid = f"dashboard-touch-{model._meta.label_lower}"
        post_save.connect(_on_change, sender=model, dispatch_uid=f"{uid}-save")
        post_delete.connect(_on_change, sender=model, dispatch_uid=f"{uid}-delete")

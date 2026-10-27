"""Manual transaction entry, edits and soft deletion."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.audit.recorder import record
from apps.common.money import Money

from ..domain import (
    MAX_DESCRIPTION_LENGTH,
    MAX_MERCHANT_LENGTH,
    MAX_REFERENCE_LENGTH,
    KindError,
    normalize_merchant_name,
    resolve_kind,
)
from ..errors import DuplicateTransactionError
from ..fingerprint import import_fingerprint, manual_fingerprint
from ..models import (
    Category,
    CategorySource,
    FinancialAccount,
    Transaction,
    Verification,
)
from .lookups import resolve_owned
from .taxonomy import ensure_merchants

_UNSET: object = object()


def owner_zone(owner: User) -> ZoneInfo:
    return ZoneInfo(owner.timezone)


def resolve_moment(
    owner: User, *, posted_at: datetime | None, posted_on: date | None
) -> tuple[datetime, date, bool]:
    """Return (utc instant, local calendar date, has_time). Exactly one input is required."""
    zone = owner_zone(owner)
    if posted_at is not None and posted_on is None:
        if timezone.is_naive(posted_at):
            raise ValidationError({"posted_at": ["Must include a UTC offset."]})
        instant = posted_at.astimezone(UTC)
        return instant, instant.astimezone(zone).date(), True
    if posted_on is not None and posted_at is None:
        local_midnight = datetime.combine(posted_on, time.min, tzinfo=zone)
        return local_midnight.astimezone(UTC), posted_on, False
    raise ValidationError({"posted_at": ["Provide exactly one of posted_at or posted_on."]})


def _check_text(field: str, value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) > limit:
        raise ValidationError({field: [f"At most {limit} characters."]})
    return cleaned


def _kind(hint: str | None, amount_minor: int) -> str:
    try:
        return resolve_kind(hint, amount_minor)
    except KindError as exc:
        raise ValidationError({"kind": [exc.detail]}) from exc


def create_transaction(
    owner: User,
    *,
    account_id: uuid.UUID,
    amount: Money,
    posted_at: datetime | None = None,
    posted_on: date | None = None,
    kind_hint: str | None = None,
    description: str = "",
    merchant_name: str = "",
    category_id: uuid.UUID | None = None,
    provider_ref: str = "",
) -> Transaction:
    account = resolve_owned(FinancialAccount, owner, account_id, "account")
    if amount.currency != account.currency:
        raise ValidationError({"amount": [f"Must be in {account.currency}."]})
    kind = _kind(kind_hint, amount.amount_minor)
    instant, local_date, has_time = resolve_moment(owner, posted_at=posted_at, posted_on=posted_on)
    description = _check_text("description", description, MAX_DESCRIPTION_LENGTH)
    merchant_name = _check_text("merchant", merchant_name, MAX_MERCHANT_LENGTH)
    provider_ref = _check_text("provider_ref", provider_ref, MAX_REFERENCE_LENGTH)
    category = resolve_owned(Category, owner, category_id, "category") if category_id else None

    # A manual entry with a provider reference deliberately collides with an imported row
    # of the same reference; without one the owner may record identical purchases twice.
    fingerprint = (
        import_fingerprint(
            owner_id=owner.pk,
            account_id=account.pk,
            posted_on=local_date,
            amount_minor=amount.amount_minor,
            currency=amount.currency,
            description=description,
            provider_ref=provider_ref,
        )
        if provider_ref
        else manual_fingerprint()
    )
    try:
        with transaction.atomic():
            merchant = ensure_merchants(owner, [merchant_name]).get(
                normalize_merchant_name(merchant_name)
            )
            tx = Transaction.objects.create(
                owner=owner,
                account=account,
                posted_at=instant,
                posted_on=local_date,
                has_time=has_time,
                amount_minor=amount.amount_minor,
                currency=amount.currency,
                kind=kind,
                description=description,
                merchant=merchant,
                category=category,
                category_source=CategorySource.MANUAL if category else CategorySource.NONE,
                provider_ref=provider_ref,
                fingerprint=fingerprint,
                verification=Verification.VERIFIED,
            )
            record(
                "finance.transaction_created",
                owner_id=owner.pk,
                target_type="transaction",
                target_id=tx.pk,
                detail={"source": "manual", "kind": kind},
            )
    except IntegrityError as exc:
        raise DuplicateTransactionError() from exc
    return tx


def update_transaction(
    owner: User,
    tx: Transaction,
    *,
    description: str | None = None,
    merchant_name: str | None = None,
    category_id: uuid.UUID | object | None = _UNSET,
    verification: str | None = None,
) -> Transaction:
    """Only descriptive fields change. Amount, date, kind and account are immutable:
    correct a wrong entry by deleting it and recording a new one, which keeps the audit
    trail and fingerprints honest."""
    changed: list[str] = []
    if description is not None:
        tx.description = _check_text("description", description, MAX_DESCRIPTION_LENGTH)
        changed.append("description")
    if merchant_name is not None:
        cleaned = _check_text("merchant", merchant_name, MAX_MERCHANT_LENGTH)
        tx.merchant = (
            ensure_merchants(owner, [cleaned]).get(normalize_merchant_name(cleaned))
            if cleaned
            else None
        )
        changed.append("merchant")
    if category_id is not _UNSET:
        if category_id is None:
            tx.category = None
            tx.category_source = CategorySource.NONE
        elif isinstance(category_id, uuid.UUID):
            tx.category = resolve_owned(Category, owner, category_id, "category")
            tx.category_source = CategorySource.MANUAL
        else:
            raise ValidationError({"category": ["Must be an id or null."]})
        changed += ["category", "category_source"]
    if verification is not None:
        if verification not in Verification.values:
            raise ValidationError({"verification": ["Invalid value."]})
        tx.verification = verification
        changed.append("verification")
    if not changed:
        return tx
    with transaction.atomic():
        tx.save(update_fields=[*changed, "updated_at"])
        record(
            "finance.transaction_updated",
            owner_id=owner.pk,
            target_type="transaction",
            target_id=tx.pk,
            detail={"fields": sorted(changed)},
        )
    return tx


def delete_transaction(owner: User, tx: Transaction) -> None:
    """Soft delete. Idempotent: deleting a deleted row changes nothing."""
    if tx.deleted_at is not None:
        return
    with transaction.atomic():
        tx.soft_delete()
        record(
            "finance.transaction_deleted",
            owner_id=owner.pk,
            target_type="transaction",
            target_id=tx.pk,
        )

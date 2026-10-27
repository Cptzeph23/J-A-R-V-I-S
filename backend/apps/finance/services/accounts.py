"""Financial accounts. Currency is fixed at creation: changing it would silently
reinterpret every stored amount."""

from __future__ import annotations

from datetime import datetime

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.audit.recorder import record
from apps.common.errors import ConflictError
from apps.common.money import Money

from ..models import FinancialAccount

_UNSET: object = object()


def _check_balance(
    currency: str, balance: Money | None, balance_at: datetime | None
) -> tuple[int | None, datetime | None]:
    if (balance is None) != (balance_at is None):
        raise ValidationError(
            {"reported_balance": ["reported_balance and reported_balance_at go together."]}
        )
    if balance is None:
        return None, None
    if balance.currency != currency:
        raise ValidationError({"reported_balance": [f"Must be in {currency}."]})
    return balance.amount_minor, balance_at


def create_account(
    owner: User,
    *,
    name: str,
    account_type: str,
    currency: str,
    include_in_spend: bool = True,
    reported_balance: Money | None = None,
    reported_balance_at: datetime | None = None,
) -> FinancialAccount:
    minor, at = _check_balance(currency, reported_balance, reported_balance_at)
    try:
        with transaction.atomic():
            account = FinancialAccount.objects.create(
                owner=owner,
                name=name,
                type=account_type,
                currency=currency,
                include_in_spend=include_in_spend,
                reported_balance_minor=minor,
                reported_balance_at=at,
            )
            record(
                "finance.account_created",
                owner_id=owner.pk,
                target_type="financial_account",
                target_id=account.pk,
                detail={"type": account_type, "currency": currency},
            )
    except IntegrityError as exc:
        raise ConflictError("An account with this name already exists.") from exc
    return account


def update_account(
    account: FinancialAccount,
    *,
    name: str | None = None,
    account_type: str | None = None,
    include_in_spend: bool | None = None,
    reported_balance: Money | object | None = _UNSET,
    reported_balance_at: datetime | object | None = _UNSET,
) -> FinancialAccount:
    changed: list[str] = []
    if name is not None:
        account.name = name
        changed.append("name")
    if account_type is not None:
        account.type = account_type
        changed.append("type")
    if include_in_spend is not None:
        account.include_in_spend = include_in_spend
        changed.append("include_in_spend")
    if reported_balance is not _UNSET or reported_balance_at is not _UNSET:
        balance = account.reported_balance_minor
        new_money = (
            Money(balance, account.currency)
            if reported_balance is _UNSET and balance is not None
            else reported_balance
        )
        new_at = (
            account.reported_balance_at if reported_balance_at is _UNSET else reported_balance_at
        )
        minor, at = _check_balance(
            account.currency,
            new_money if isinstance(new_money, Money) else None,
            new_at if isinstance(new_at, datetime) else None,
        )
        account.reported_balance_minor = minor
        account.reported_balance_at = at
        changed += ["reported_balance_minor", "reported_balance_at"]
    if not changed:
        return account
    try:
        with transaction.atomic():
            account.save(update_fields=[*changed, "updated_at"])
            record(
                "finance.account_updated",
                owner_id=account.owner_id,
                target_type="financial_account",
                target_id=account.pk,
                detail={"fields": sorted(changed)},
            )
    except IntegrityError as exc:
        raise ConflictError("An account with this name already exists.") from exc
    return account

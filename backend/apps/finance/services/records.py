"""Load an owner's accounts, policy and transactions as the calculators' plain inputs."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from apps.accounts.models import User
from apps.common.money import Money

from ..calculators.cashflow import AccountInfo, Policy, TxRecord
from ..models import Category, FinancialAccount, Transaction
from .taxonomy import get_policy


def load_accounts(owner: User) -> dict[uuid.UUID, AccountInfo]:
    return {
        a.pk: AccountInfo(
            id=a.pk, name=a.name, currency=a.currency, include_in_spend=a.include_in_spend
        )
        for a in FinancialAccount.objects.for_owner(owner)
    }


def load_policy(owner: User) -> Policy:
    return Policy(treat_transfers_as_spend=get_policy(owner).treat_transfers_as_spend)


def load_records(owner: User, *, start: date, end: date) -> list[TxRecord]:
    rows = (
        Transaction.objects.live_for_owner(owner)
        .filter(posted_on__gte=start, posted_on__lte=end)
        .select_related("merchant")
        .order_by("posted_on", "id")
    )
    return [
        TxRecord(
            id=t.pk,
            account_id=t.account_id,
            posted_on=t.posted_on,
            amount=Money(t.amount_minor, t.currency),
            kind=t.kind,
            category_id=t.category_id,
            merchant=t.merchant.name if t.merchant else None,
            verified=t.verification == "verified",
        )
        for t in rows
    ]


def category_names(owner: User) -> dict[uuid.UUID, str]:
    return dict(Category.objects.for_owner(owner).values_list("pk", "name"))


def owner_today(owner: User, now: datetime) -> date:
    return now.astimezone(ZoneInfo(owner.timezone)).date()

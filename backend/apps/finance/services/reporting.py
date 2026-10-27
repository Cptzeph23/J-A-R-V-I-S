"""Assemble a monthly report from stored data. The arithmetic lives in calculators/."""

from __future__ import annotations

import uuid
from typing import Any

from apps.accounts.models import User
from apps.common.money import Money

from ..calculators.cashflow import AccountInfo, Policy, TxRecord
from ..models import Category, FinancialAccount, Transaction
from ..reports import build_monthly_report, month_bounds
from ..selectors.transactions import latest_data_timestamp
from .taxonomy import get_policy


def monthly_report(owner: User, *, month: str, currency: str | None = None) -> dict[str, Any]:
    start, end = month_bounds(month)  # raises InvalidMonthError for a bad month
    chosen = currency or owner.default_currency
    accounts = {
        a.pk: AccountInfo(
            id=a.pk, name=a.name, currency=a.currency, include_in_spend=a.include_in_spend
        )
        for a in FinancialAccount.objects.for_owner(owner)
    }
    rows = (
        Transaction.objects.live_for_owner(owner)
        .filter(posted_on__gte=start, posted_on__lte=end)
        .select_related("merchant")
        .order_by("posted_on", "id")
    )
    records = [
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
    names: dict[uuid.UUID, str] = dict(Category.objects.for_owner(owner).values_list("pk", "name"))
    policy = Policy(treat_transfers_as_spend=get_policy(owner).treat_transfers_as_spend)
    return build_monthly_report(
        month=month,
        records=records,
        accounts=accounts,
        policy=policy,
        currency=chosen,
        category_names=names,
        data_as_of=latest_data_timestamp(owner),
    )

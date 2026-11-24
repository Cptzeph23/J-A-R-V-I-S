"""Database builders and a pinned clock for advisor tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from apps.accounts.models import User
from apps.finance.models import Category, FinancialAccount, Merchant, Transaction
from tests.factories import (
    CategoryFactory,
    FinancialAccountFactory,
    MerchantFactory,
    TransactionFactory,
)

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)  # 12:00 in Nairobi
TODAY = date(2026, 11, 20)


@pytest.fixture
def pinned_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Pin the clock the advisor and budget views read (not the auth clock)."""
    clock = SimpleNamespace(now=lambda: NOW)
    monkeypatch.setattr("apps.advisor.api.views.timezone", clock)
    monkeypatch.setattr("apps.finance.api.views.timezone", clock)
    return NOW


def account(owner: User) -> FinancialAccount:
    existing = FinancialAccount.objects.filter(owner=owner).first()
    return existing or FinancialAccountFactory(owner=owner, name="Main")


def merchant(owner: User, name: str) -> Merchant:
    found = Merchant.objects.filter(owner=owner, name=name).first()
    return found or MerchantFactory(owner=owner, name=name, normalized_name=name.casefold())


def spend(
    owner: User,
    on: date,
    minor: int,
    *,
    merchant_name: str | None = None,
    category: Category | None = None,
    kind: str = "expense",
) -> Transaction:
    inflow = kind in {"income", "refund", "transfer_in"}
    return TransactionFactory(
        owner=owner,
        account=account(owner),
        posted_on=on,
        posted_at=datetime(on.year, on.month, on.day, 9, 0, tzinfo=UTC),
        amount_minor=minor if inflow else -minor,
        kind=kind,
        merchant=merchant(owner, merchant_name) if merchant_name else None,
        category=category,
    )


def subscription(owner: User, name: str, minor: int, *, last_month: int = 11, months: int = 6):
    """A charge on the 5th of each of `months` months ending at `last_month` of 2026."""
    rows = []
    for i in range(months):
        month = last_month - i
        year = 2026
        while month < 1:
            month += 12
            year -= 1
        rows.append(spend(owner, date(year, month, 5), minor, merchant_name=name))
    return rows


def category(owner: User, name: str, *, essential: bool = False) -> Category:
    return CategoryFactory(owner=owner, name=name, essential=essential)

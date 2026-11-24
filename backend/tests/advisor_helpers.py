"""Builders for advisor tests: plain TxRecords for the pure calculators."""

from __future__ import annotations

import uuid
from datetime import date

from apps.common.money import Money
from apps.finance.calculators.cashflow import AccountInfo, Policy, TxRecord

ACCOUNT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
ACCOUNTS = {ACCOUNT_ID: AccountInfo(id=ACCOUNT_ID, name="Main", currency="KES")}
POLICY = Policy()


def rec(
    on: date,
    minor: int,
    *,
    merchant: str | None = None,
    category: uuid.UUID | None = None,
    kind: str = "expense",
    currency: str = "KES",
    account: uuid.UUID = ACCOUNT_ID,
) -> TxRecord:
    """`minor` is a positive size; the sign is applied from the kind."""
    inflow = kind in {"income", "transfer_in", "refund"}
    return TxRecord(
        id=uuid.uuid4(),
        account_id=account,
        posted_on=on,
        amount=Money(minor if inflow else -minor, currency),
        kind=kind,
        category_id=category,
        merchant=merchant,
    )

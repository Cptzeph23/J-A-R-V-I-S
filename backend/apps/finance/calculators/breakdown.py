"""Spend breakdown for dashboards: by merchant, category, account, day and hour.

Pure and deterministic like `cashflow`, and it shares that module's inclusion rules
(`exclusion_reason`), so the dashboard total is always exactly the cash-flow `expenses` figure
for the same inputs (SRS-DSH-02). Every breakdown dimension partitions the same set of
records, so each one sums to the total; records with no category or no merchant land in an
explicit `None` bucket instead of disappearing.

Spend value of a record: outflows (expense, fee, and transfer_out when the owner's policy
treats transfers as spending) add |amount|; a refund subtracts its amount; income and
transfer_in are not spending and are ignored here.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, tzinfo

from apps.common.money import Money

from .cashflow import AccountInfo, Exclusion, Policy, TxRecord, exclusion_reason

TOP_N = 5
_OUTFLOW = frozenset({"expense", "fee", "transfer_out"})


@dataclass(frozen=True, slots=True)
class MerchantSpend:
    merchant: str | None
    total: Money
    count: int


@dataclass(frozen=True, slots=True)
class TopSpend:
    record: TxRecord
    spend: Money


@dataclass(frozen=True, slots=True)
class SpendBreakdown:
    currency: str
    total: Money
    by_merchant: tuple[MerchantSpend, ...]
    by_category: Mapping[uuid.UUID | None, Money]
    by_account: Mapping[uuid.UUID, Money]
    by_day: Mapping[date, tuple[Money, int]]
    by_hour: Mapping[int, Money]
    untimed: Money  # spend from records with no time of day (statement rows)
    top: tuple[TopSpend, ...]
    exclusions: tuple[Exclusion, ...]
    included_count: int  # records that contributed to spending
    unverified_count: int


def _spend_minor(record: TxRecord, policy: Policy) -> int | None:
    """Signed contribution to spending, or None when the record is not spending at all."""
    kind = record.kind
    if kind in _OUTFLOW and (kind != "transfer_out" or policy.treat_transfers_as_spend):
        return -record.amount.amount_minor
    if kind == "refund":
        return -record.amount.amount_minor
    return None


def spend_breakdown(
    records: Iterable[TxRecord],
    accounts: Mapping[uuid.UUID, AccountInfo],
    policy: Policy,
    *,
    currency: str,
    period_start: date,
    period_end: date,
    zone: tzinfo,
) -> SpendBreakdown:
    by_category: dict[uuid.UUID | None, int] = defaultdict(int)
    by_account: dict[uuid.UUID, int] = defaultdict(int)
    by_day: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    by_hour: dict[int, int] = defaultdict(int)
    merchants: dict[str | None, list[int]] = defaultdict(lambda: [0, 0])
    exclusions: list[Exclusion] = []
    outflows: list[tuple[int, TxRecord]] = []
    untimed = 0
    included = 0
    unverified = 0

    for record in records:
        reason = exclusion_reason(record, accounts, policy, currency, period_start, period_end)
        if reason is not None:
            exclusions.append(Exclusion(record.id, reason))
            continue
        spend = _spend_minor(record, policy)
        if spend is None:
            continue
        included += 1
        if not record.verified:
            unverified += 1
        by_category[record.category_id] += spend
        by_account[record.account_id] += spend
        day = by_day[record.posted_on]
        day[0] += spend
        day[1] += 1
        entry = merchants[record.merchant]
        entry[0] += spend
        if spend > 0:
            entry[1] += 1
            outflows.append((spend, record))
        if record.has_time and record.posted_at is not None:
            by_hour[record.posted_at.astimezone(zone).hour] += spend
        else:
            untimed += spend

    total = sum(by_category.values())

    def money(minor: int) -> Money:
        return Money(minor, currency)

    merchant_rows = sorted(
        (MerchantSpend(name, money(v[0]), v[1]) for name, v in merchants.items()),
        key=lambda m: (-m.total.amount_minor, m.merchant is None, m.merchant or ""),
    )
    top = sorted(outflows, key=lambda p: (-p[0], p[1].posted_on, str(p[1].id)))[:TOP_N]
    return SpendBreakdown(
        currency=currency,
        total=money(total),
        by_merchant=tuple(merchant_rows),
        by_category={k: money(v) for k, v in by_category.items()},
        by_account={k: money(v) for k, v in by_account.items()},
        by_day={k: (money(v[0]), v[1]) for k, v in by_day.items()},
        by_hour={k: money(v) for k, v in by_hour.items()},
        untimed=money(untimed),
        top=tuple(TopSpend(r, money(s)) for s, r in top),
        exclusions=tuple(sorted(exclusions, key=lambda e: (str(e.transaction_id), e.reason))),
        included_count=included,
        unverified_count=unverified,
    )

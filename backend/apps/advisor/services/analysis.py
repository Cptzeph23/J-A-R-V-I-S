"""Assemble the calculators' inputs from stored data and run them."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from apps.accounts.models import User
from apps.common.money import Money
from apps.finance.calculators.anomalies import AnomalyReport, detect_anomalies
from apps.finance.calculators.cashflow import compute_cashflow
from apps.finance.calculators.recurring import LOOKBACK_DAYS, RecurringReport, detect_recurring
from apps.finance.models import Category
from apps.finance.services import budgets as budget_service
from apps.finance.services import records

from ..engine import BudgetHistory
from ..scenarios import Baseline

BASELINE_MONTHS = 6
HISTORY_MONTHS_FOR_BUDGETS = 3


def month_start(day: date) -> date:
    return day.replace(day=1)


def previous_month(first: date) -> date:
    return month_start(first - timedelta(days=1))


def month_end(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def complete_months(today: date, count: int) -> list[date]:
    """First days of the `count` most recent complete months, oldest first."""
    months: list[date] = []
    cursor = previous_month(month_start(today))
    for _ in range(count):
        months.append(cursor)
        cursor = previous_month(cursor)
    return months[::-1]


def recurring(owner: User, *, today: date, currency: str) -> RecurringReport:
    rows = records.load_records(owner, start=today - timedelta(days=LOOKBACK_DAYS), end=today)
    return detect_recurring(
        rows,
        records.load_accounts(owner),
        records.load_policy(owner),
        currency=currency,
        as_of=today,
    )


def anomalies(owner: User, *, month: date, currency: str) -> AnomalyReport:
    first = month_start(month)
    # Seven months back covers the six months of history the detector uses.
    start = first
    for _ in range(7):
        start = previous_month(start)
    rows = records.load_records(owner, start=start, end=month_end(first))
    return detect_anomalies(
        rows,
        records.load_accounts(owner),
        records.load_policy(owner),
        currency=currency,
        month_start=first,
    )


def baseline(owner: User, *, today: date, currency: str, months: int = BASELINE_MONTHS) -> Baseline:
    """Average of the most recent complete months that actually hold data."""
    window = complete_months(today, months)
    rows = records.load_records(owner, start=window[0], end=month_end(window[-1]))
    accounts = records.load_accounts(owner)
    policy = records.load_policy(owner)
    flows = [
        compute_cashflow(
            rows, accounts, policy, currency=currency, period_start=m, period_end=month_end(m)
        )
        for m in window
    ]
    used = [f for f in flows if f.included_count > 0]
    zero = Money.zero(currency)
    if not used:
        return Baseline(currency, 0, zero, zero, {}, zero, zero)
    n = len(used)
    income = sum(f.income.amount_minor for f in used) // n
    expenses = sum(f.expenses.amount_minor for f in used) // n
    totals: dict[uuid.UUID | None, int] = {}
    for f in used:
        for cid, money in f.expenses_by_category.items():
            totals[cid] = totals.get(cid, 0) + money.amount_minor
    nets = [f.net.amount_minor for f in used]
    return Baseline(
        currency=currency,
        months=n,
        income=Money(income, currency),
        expenses=Money(expenses, currency),
        expenses_by_category={k: Money(v // n, currency) for k, v in totals.items()},
        net_min=Money(min(nets), currency),
        net_max=Money(max(nets), currency),
    )


def budget_histories(owner: User, *, today: date, currency: str) -> tuple[BudgetHistory, ...]:
    from apps.finance.models import Budget

    window = complete_months(today, HISTORY_MONTHS_FOR_BUDGETS)
    labels = budget_service.labels(owner)
    current = {
        s.budget_id: s
        for s in budget_service.status(
            owner, month=today.strftime("%Y-%m"), today=today, currency=currency
        ).statuses
    }
    past = {
        m: {
            s.budget_id: s
            for s in budget_service.status(
                owner, month=m.strftime("%Y-%m"), today=today, currency=currency
            ).statuses
        }
        for m in window
    }
    result: list[BudgetHistory] = []
    for budget in Budget.objects.for_owner(owner).filter(currency=currency).order_by("id"):
        months = tuple(
            (m.strftime("%Y-%m"), past[m][budget.pk].spent)
            for m in window
            if budget.pk in past[m] and _has_data(past[m][budget.pk])
        )
        result.append(
            BudgetHistory(
                budget_id=budget.pk,
                label=labels.get(budget.pk, "Budget"),
                category_id=budget.category_id,
                limit=budget.limit,
                months=months,
                current=current.get(budget.pk),
            )
        )
    return tuple(result)


def _has_data(status: object) -> bool:
    # A month with no spending at all in the budget's scope is treated as "no data", so a
    # month before the owner started recording cannot look like a month that stayed in budget.
    spent = getattr(status, "spent", None)
    return spent is not None and spent.amount_minor > 0


def essential_categories(owner: User) -> frozenset[uuid.UUID]:
    return frozenset(
        Category.objects.for_owner(owner).filter(essential=True).values_list("pk", flat=True)
    )

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from apps.common.money import Money
from apps.finance.calculators.budgets import BudgetSpec, evaluate_budgets
from tests.advisor_helpers import ACCOUNTS, POLICY, rec

FOOD = uuid.UUID("00000000-0000-0000-0000-0000000000f1")
SNACKS = uuid.UUID("00000000-0000-0000-0000-0000000000f3")
RENT = uuid.UUID("00000000-0000-0000-0000-0000000000f4")
FOOD_BUDGET = BudgetSpec(uuid.uuid4(), Money(1000000, "KES"), frozenset({FOOD, SNACKS}))
ALL_BUDGET = BudgetSpec(uuid.uuid4(), Money(5000000, "KES"), None)
MONTH = date(2026, 11, 1)


def run(rows, today, specs=(FOOD_BUDGET, ALL_BUDGET)):
    return evaluate_budgets(
        specs, rows, ACCOUNTS, POLICY, currency="KES", month_start=MONTH, today=today
    )


def by_id(report):
    return {s.budget_id: s for s in report.statuses}


def test_category_budget_includes_sub_categories_and_overall_counts_everything():
    rows = [
        rec(date(2026, 11, 2), 300000, category=FOOD),
        rec(date(2026, 11, 3), 200000, category=SNACKS),
        rec(date(2026, 11, 4), 900000, category=RENT),
    ]
    s = by_id(run(rows, date(2026, 11, 5)))
    assert s[FOOD_BUDGET.id].spent == Money(500000, "KES")
    assert s[FOOD_BUDGET.id].remaining == Money(500000, "KES")
    assert s[FOOD_BUDGET.id].used_share == Decimal("0.5000")
    assert s[ALL_BUDGET.id].spent == Money(1400000, "KES")


def test_over_budget_state_and_negative_remaining():
    rows = [rec(date(2026, 11, 2), 1200000, category=FOOD)]
    status = by_id(run(rows, date(2026, 11, 28)))[FOOD_BUDGET.id]
    assert status.state == "over"
    assert status.remaining == Money(-200000, "KES")
    assert status.used_share == Decimal("1.2000")


def test_at_risk_when_eighty_percent_used():
    rows = [rec(date(2026, 11, 2), 800000, category=FOOD)]
    assert by_id(run(rows, date(2026, 11, 3)))[FOOD_BUDGET.id].state == "at_risk"


def test_projection_flags_a_budget_on_pace_to_exceed():
    # 500,000 spent in 10 days of a 30-day month projects to 1,500,000 > 1,000,000.
    rows = [rec(date(2026, 11, 2), 500000, category=FOOD)]
    status = by_id(run(rows, date(2026, 11, 10)))[FOOD_BUDGET.id]
    assert status.projected == Money(1500000, "KES")
    assert status.projected_share == Decimal("1.5000")
    assert status.state == "at_risk"
    assert (status.days_elapsed, status.days_in_month) == (10, 30)


def test_no_projection_before_day_seven_or_after_the_month():
    rows = [rec(date(2026, 11, 2), 500000, category=FOOD)]
    assert by_id(run(rows, date(2026, 11, 6)))[FOOD_BUDGET.id].projected is None
    assert by_id(run(rows, date(2026, 12, 3)))[FOOD_BUDGET.id].projected is None


def test_completed_month_uses_the_full_month_and_future_month_has_not_started():
    rows = [rec(date(2026, 11, 2), 400000, category=FOOD)]
    done = by_id(run(rows, date(2026, 12, 3)))[FOOD_BUDGET.id]
    assert (done.state, done.days_elapsed) == ("ok", 30)
    future = by_id(run([], date(2026, 10, 15)))[FOOD_BUDGET.id]
    assert future.state == "not_started"


def test_refunds_subtract_and_transfers_are_not_spending():
    rows = [
        rec(date(2026, 11, 2), 600000, category=FOOD),
        rec(date(2026, 11, 3), 100000, category=FOOD, kind="refund"),
        rec(date(2026, 11, 3), 900000, category=FOOD, kind="transfer_out"),
    ]
    assert by_id(run(rows, date(2026, 11, 4)))[FOOD_BUDGET.id].spent == Money(500000, "KES")


def test_budget_in_another_currency_is_skipped():
    usd = BudgetSpec(uuid.uuid4(), Money(100000, "USD"), None)
    assert run([], date(2026, 11, 5), specs=(usd,)).statuses == ()


def test_budget_total_equals_the_monthly_report_expenses():
    from apps.finance.calculators.cashflow import compute_cashflow

    rows = [
        rec(date(2026, 11, 2), 123456, category=FOOD),
        rec(date(2026, 11, 9), 654321, category=RENT),
        rec(date(2026, 11, 9), 5000, category=RENT, kind="refund"),
    ]
    flow = compute_cashflow(
        rows, ACCOUNTS, POLICY, currency="KES", period_start=MONTH, period_end=date(2026, 11, 30)
    )
    assert by_id(run(rows, date(2026, 11, 20)))[ALL_BUDGET.id].spent == flow.expenses

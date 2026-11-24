from __future__ import annotations

import uuid
from datetime import date, timedelta

from apps.common.money import Money
from apps.finance.calculators.recurring import (
    MIN_OCCURRENCES,
    _add_months,
    detect_recurring,
    monthly_equivalent,
)
from tests.advisor_helpers import ACCOUNT_ID, ACCOUNTS, POLICY, rec

AS_OF = date(2026, 11, 20)


def detect(records, as_of=AS_OF):
    return detect_recurring(records, ACCOUNTS, POLICY, currency="KES", as_of=as_of)


def monthly(merchant="Netflix", amount=120000, months=6, day=5, **kw):
    out = []
    for i in range(months):
        month = AS_OF.month - 1 - i
        year = AS_OF.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        out.append(rec(date(year, month, day), amount, merchant=merchant, **kw))
    return out


def test_fixed_monthly_charge_is_detected_with_evidence():
    report = detect(monthly())
    assert len(report.charges) == 1
    charge = report.charges[0]
    assert charge.merchant == "Netflix"
    assert charge.cadence == "monthly"
    assert charge.amount_pattern == "fixed"
    assert charge.typical_amount == Money(120000, "KES")
    assert charge.monthly_equivalent == Money(120000, "KES")
    assert charge.occurrences == 6
    assert charge.confidence == "high"
    assert charge.active is True
    assert charge.next_expected == date(2026, 11, 5)
    assert len(charge.evidence_dates) == 6
    assert report.active_monthly_total == Money(120000, "KES")


def test_fewer_than_three_charges_is_not_recurring():
    assert MIN_OCCURRENCES == 3
    assert detect(monthly(months=2)).charges == ()


def test_weekly_charge_monthly_equivalent_is_scaled():
    rows = [rec(AS_OF - timedelta(days=7 * i + 2), 30000, merchant="Gym") for i in range(8)]
    charge = detect(rows).charges[0]
    assert charge.cadence == "weekly"
    # 30000 x 52 / 12 = 130000
    assert charge.monthly_equivalent == Money(130000, "KES")


def test_monthly_equivalent_for_each_cadence():
    amount = Money(120000, "KES")
    assert monthly_equivalent(amount, "weekly").amount_minor == 520000
    assert monthly_equivalent(amount, "biweekly").amount_minor == 260000
    assert monthly_equivalent(amount, "monthly").amount_minor == 120000
    assert monthly_equivalent(amount, "quarterly").amount_minor == 40000
    assert monthly_equivalent(amount, "yearly").amount_minor == 10000


def test_variable_amounts_within_tolerance_are_reported_as_variable():
    amounts = [100000, 118000, 90000, 112000, 105000, 95000]
    rows = [
        rec(date(2026, 10 - i if i < 10 else 1, 3), a, merchant="Power")
        for i, a in enumerate(amounts[:5])
    ] + [rec(date(2026, 5, 3), amounts[5], merchant="Power")]
    charge = detect(rows).charges[0]
    assert charge.amount_pattern == "variable"
    assert charge.confidence != "high"


def test_erratic_amounts_are_not_reported():
    amounts = [10000, 90000, 20000, 150000, 5000, 70000]
    rows = [rec(date(2026, 10 - i, 3), a, merchant="Random") for i, a in enumerate(amounts)]
    assert detect(rows).charges == ()


def test_irregular_gaps_are_not_reported():
    days = [date(2026, 1, 3), date(2026, 1, 20), date(2026, 3, 9), date(2026, 3, 11)]
    rows = [rec(d, 50000, merchant="Odd") for d in days]
    assert detect(rows).charges == ()


def test_habit_of_frequent_small_purchases_is_not_a_subscription():
    rows = [rec(AS_OF - timedelta(days=i), 5000, merchant="Cafe") for i in range(30)]
    assert detect(rows).charges == ()


def test_same_day_charges_are_one_occurrence():
    rows = monthly(months=4)
    rows.append(rec(rows[0].posted_on, 120000, merchant="Netflix"))
    charge = detect(rows).charges[0]
    assert charge.occurrences == 4
    assert charge.evidence_amounts[-1] == Money(240000, "KES")


def test_lapsed_charge_is_inactive_and_excluded_from_the_total():
    rows = [rec(date(2026, m, 5), 50000, merchant="Old gym") for m in (3, 4, 5, 6)]
    report = detect(rows)
    assert report.charges[0].active is False
    assert report.active_monthly_total == Money(0, "KES")


def test_transfers_income_refunds_and_merchantless_rows_are_ignored():
    rows = (
        monthly(kind="transfer_out")
        + monthly(kind="income", merchant="Employer")
        + monthly(kind="refund", merchant="Shop")
        + monthly(merchant=None)
    )
    assert detect(rows).charges == ()


def test_other_currency_and_unknown_accounts_are_excluded():
    rows = monthly(currency="USD") + monthly(account=uuid.uuid4())
    assert detect(rows).charges == ()


def test_merchant_names_match_ignoring_case_and_spaces():
    rows = monthly(months=3)
    rows[1] = rec(rows[1].posted_on, 120000, merchant="  NETFLIX ")
    assert detect(rows).charges[0].occurrences == 3


def test_results_are_ordered_by_cost_then_name_and_deterministic():
    rows = monthly("Small", 20000) + monthly("Big", 900000) + monthly("Alpha", 20000)
    first = detect(rows)
    assert [c.merchant for c in first.charges] == ["Big", "Alpha", "Small"]
    assert detect(list(reversed(rows))).charges == first.charges


def test_month_end_clamping_for_next_expected():
    assert _add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert _add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert _add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert _add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


def test_assumptions_are_always_stated():
    report = detect([])
    assert report.charges == ()
    assert any("pattern" in a for a in report.assumptions)
    assert ACCOUNT_ID in ACCOUNTS

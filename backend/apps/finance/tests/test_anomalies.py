from __future__ import annotations

import uuid
from datetime import date

from apps.common.money import Money
from apps.finance.calculators.anomalies import (
    MIN_HISTORY_MONTHS,
    detect_anomalies,
)
from tests.advisor_helpers import ACCOUNTS, POLICY, rec

FOOD = uuid.UUID("00000000-0000-0000-0000-0000000000f1")
FUEL = uuid.UUID("00000000-0000-0000-0000-0000000000f2")
MONTH = date(2026, 11, 1)


def history(category, amounts, *, start_month=10):
    """One purchase on the 10th of each month going back from `start_month`."""
    rows = []
    for i, amount in enumerate(amounts):
        month = start_month - i
        year = 2026
        while month < 1:
            month += 12
            year -= 1
        rows.append(rec(date(year, month, 10), amount, category=category, merchant="Shop"))
    return rows


def run(rows, **kw):
    return detect_anomalies(rows, ACCOUNTS, POLICY, currency="KES", month_start=MONTH, **kw)


def test_category_far_above_its_usual_month_is_flagged():
    rows = history(FOOD, [300000, 310000, 290000, 305000, 295000, 300000])
    rows.append(rec(date(2026, 11, 8), 700000, category=FOOD))
    report = run(rows)
    assert len(report.categories) == 1
    item = report.categories[0]
    assert item.category_id == FOOD
    assert item.spent == Money(700000, "KES")
    assert item.usual == Money(300000, "KES")
    assert item.excess == Money(400000, "KES")
    assert str(item.ratio) == "2.33"
    assert item.history_months == 6


def test_normal_month_is_not_flagged():
    rows = history(FOOD, [300000, 310000, 290000, 305000, 295000, 300000])
    rows.append(rec(date(2026, 11, 8), 320000, category=FOOD))
    assert run(rows).categories == ()


def test_too_little_history_is_reported_not_guessed():
    assert MIN_HISTORY_MONTHS == 3
    rows = history(FOOD, [300000, 310000])
    rows.append(rec(date(2026, 11, 8), 900000, category=FOOD))
    report = run(rows)
    assert report.categories == ()
    assert report.insufficient_history == (FOOD,)


def test_a_single_odd_month_in_history_does_not_hide_a_real_anomaly():
    rows = history(FOOD, [300000, 300000, 2000000, 300000, 300000, 300000])
    rows.append(rec(date(2026, 11, 8), 800000, category=FOOD))
    assert [a.category_id for a in run(rows).categories] == [FOOD]


def test_small_absolute_differences_are_ignored():
    rows = history(FUEL, [10000, 10000, 10000, 10000, 10000, 10000])
    rows.append(rec(date(2026, 11, 8), 40000, category=FUEL))  # 4x, but only KES 300 more
    assert run(rows).categories == ()


def test_refunds_reduce_the_month():
    rows = history(FOOD, [300000, 310000, 290000, 305000, 295000, 300000])
    rows.append(rec(date(2026, 11, 8), 700000, category=FOOD))
    rows.append(rec(date(2026, 11, 9), 450000, category=FOOD, kind="refund"))
    assert run(rows).categories == ()


def test_single_purchase_far_above_the_usual_purchase_is_flagged():
    rows = [
        rec(date(2026, 10, d), a, category=FOOD, merchant="Shop")
        for d, a in zip(
            (1, 5, 9, 13, 17, 21), (20000, 21000, 19000, 20500, 19500, 20000), strict=True
        )
    ]
    big = rec(date(2026, 11, 3), 150000, category=FOOD, merchant="Shop")
    report = run([*rows, big])
    assert [p.transaction_id for p in report.purchases] == [big.id]
    assert report.purchases[0].usual == Money(20000, "KES")
    assert report.purchases[0].history_purchases == 6


def test_purchase_check_needs_five_earlier_purchases_and_a_category():
    few = [rec(date(2026, 10, d), 20000, category=FOOD) for d in (1, 5, 9, 13)]
    big = rec(date(2026, 11, 3), 150000, category=FOOD)
    assert run([*few, big]).purchases == ()
    many = [rec(date(2026, 10, d), 20000) for d in (1, 5, 9, 13, 17, 21)]
    assert run([*many, rec(date(2026, 11, 3), 150000)]).purchases == ()


def test_other_currency_and_transfers_are_ignored():
    rows = history(FOOD, [300000] * 6)
    rows.append(rec(date(2026, 11, 8), 900000, category=FOOD, currency="USD"))
    rows.append(rec(date(2026, 11, 8), 900000, category=FOOD, kind="transfer_out"))
    assert run(rows).categories == ()


def test_assumptions_name_the_floor_and_history_requirement():
    report = run([])
    text = " ".join(report.assumptions)
    assert "KES 500.00" in text
    assert "3 of the previous 6" in text


NOISY_MONTHS = [200000, 400000, 300000, 500000, 100000, 300000]  # median 300000, spread 148260


def test_noisy_history_widens_what_counts_as_normal():
    rows = history(FOOD, NOISY_MONTHS)
    # +300,000 is 2x usual and well over the floor, but inside 3 x 148,260.
    assert run([*rows, rec(date(2026, 11, 8), 600000, category=FOOD)]).categories == ()
    # +500,000 is beyond 3 x spread.
    flagged = run([*rows, rec(date(2026, 11, 8), 800000, category=FOOD)]).categories
    assert [a.category_id for a in flagged] == [FOOD]


def test_noisy_purchase_history_widens_what_counts_as_normal():
    amounts = [10000, 30000, 20000, 40000, 5000, 20000]  # median 20000, spread 14826
    peers = [
        rec(date(2026, 10, d), a, category=FOOD)
        for d, a in zip((1, 4, 7, 10, 13, 16), amounts, strict=True)
    ]
    inside = rec(date(2026, 11, 3), 70000, category=FOOD)  # +50,000 < 4 x 14,826
    assert run([*peers, inside]).purchases == ()
    outside = rec(date(2026, 11, 3), 90000, category=FOOD)  # +70,000 > 59,304
    assert [p.transaction_id for p in run([*peers, outside]).purchases] == [outside.id]


def test_spread_never_falls_below_ten_percent_of_usual():
    # Perfectly steady history: spread floors at 10% of usual, so the threshold is 3 x that.
    steady = history(FOOD, [300000] * 6)  # floor 30,000; flag above +90,000
    just_under = run([*steady, rec(date(2026, 11, 8), 389000, category=FOOD)]).categories
    assert just_under == ()
    over = run([*steady, rec(date(2026, 11, 8), 400000, category=FOOD)]).categories
    assert [a.category_id for a in over] == [FOOD]

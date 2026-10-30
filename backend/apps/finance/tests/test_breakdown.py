import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from apps.common.money import Money
from apps.finance.calculators.breakdown import TOP_N, spend_breakdown
from apps.finance.calculators.cashflow import AccountInfo, Policy, TxRecord, compute_cashflow

START, END = date(2026, 10, 1), date(2026, 10, 31)
NAIROBI = ZoneInfo("Africa/Nairobi")
BANK, MPESA, SAVINGS = (uuid.UUID(int=i) for i in (1, 2, 3))
FOOD, FUEL = uuid.UUID(int=101), uuid.UUID(int=102)
ACCOUNTS = {
    BANK: AccountInfo(BANK, "Bank", "KES"),
    MPESA: AccountInfo(MPESA, "M-Pesa", "KES"),
    SAVINGS: AccountInfo(SAVINGS, "Savings", "KES", include_in_spend=False),
}
SIGN = {"income": 1, "expense": -1, "fee": -1, "refund": 1, "transfer_in": 1, "transfer_out": -1}


def tx(
    kind,
    minor,
    *,
    account=BANK,
    day=date(2026, 10, 10),
    category=None,
    merchant=None,
    currency="KES",
    at=None,
    n=None,
    verified=True,
):
    return TxRecord(
        id=uuid.UUID(int=n) if n is not None else uuid.uuid4(),
        account_id=account,
        posted_on=day,
        amount=Money(SIGN[kind] * minor, currency),
        kind=kind,
        category_id=category,
        merchant=merchant,
        verified=verified,
        posted_at=at,
        has_time=at is not None,
    )


DEFAULT_POLICY = Policy()


def run(records, policy=DEFAULT_POLICY, currency="KES"):
    return spend_breakdown(
        records,
        ACCOUNTS,
        policy,
        currency=currency,
        period_start=START,
        period_end=END,
        zone=NAIROBI,
    )


def test_hand_computed_day():
    records = [
        tx("expense", 21000, category=FOOD, merchant="Naivas", n=1),
        tx("expense", 4000, category=FUEL, merchant="Shell", n=2, account=MPESA),
        tx("expense", 9000, category=FOOD, merchant="Naivas", n=3),
        tx("fee", 100, merchant=None, n=4, account=MPESA),
        tx("refund", 1000, category=FOOD, merchant="Naivas", n=5),
        tx("income", 500000, n=6),
    ]
    b = run(records)
    assert b.total == Money(33100, "KES")
    assert [(m.merchant, m.total.amount_minor, m.count) for m in b.by_merchant] == [
        ("Naivas", 29000, 2),
        ("Shell", 4000, 1),
        (None, 100, 1),
    ]
    assert dict(b.by_category) == {
        FOOD: Money(29000, "KES"),
        FUEL: Money(4000, "KES"),
        None: Money(100, "KES"),
    }
    assert dict(b.by_account) == {BANK: Money(29000, "KES"), MPESA: Money(4100, "KES")}
    assert b.included_count == 5  # income is not spending
    assert [t.record.id.int for t in b.top] == [1, 3, 2, 4]


def test_transfers_follow_the_owner_policy_and_are_reported_when_excluded():
    records = [tx("transfer_out", 5000, n=1), tx("expense", 100, n=2)]
    assert run(records).total == Money(100, "KES")
    assert [e.reason for e in run(records).exclusions] == ["transfer_excluded_by_policy"]
    assert run(records, Policy(treat_transfers_as_spend=True)).total == Money(5100, "KES")


def test_excluded_accounts_other_currencies_and_other_dates_are_left_out_with_reasons():
    records = [
        tx("expense", 100, account=SAVINGS, n=1),
        tx("expense", 200, currency="USD", n=2),
        tx("expense", 300, day=date(2026, 11, 1), n=3),
        tx("expense", 400, n=4),
    ]
    b = run(records)
    assert b.total == Money(400, "KES")
    assert {e.reason for e in b.exclusions} == {
        "account_excluded_from_spend",
        "currency_mismatch",
        "outside_period",
    }


def test_hours_use_the_requested_zone_and_untimed_rows_are_kept_apart():
    # 21:30Z is 00:30 the next day in Nairobi; 12:00Z is 15:00.
    records = [
        tx(
            "expense",
            100,
            at=datetime(2026, 10, 9, 21, 30, tzinfo=UTC),
            day=date(2026, 10, 10),
            n=1,
        ),
        tx("expense", 250, at=datetime(2026, 10, 10, 12, 0, tzinfo=UTC), n=2),
        tx("expense", 777, n=3),  # statement row: date only
    ]
    b = run(records)
    assert dict(b.by_hour) == {0: Money(100, "KES"), 15: Money(250, "KES")}
    assert b.untimed == Money(777, "KES")
    utc = spend_breakdown(
        records, ACCOUNTS, Policy(), currency="KES", period_start=START, period_end=END, zone=UTC
    )
    assert dict(utc.by_hour) == {21: Money(100, "KES"), 12: Money(250, "KES")}


def test_days_and_counts():
    d1, d2 = date(2026, 10, 3), date(2026, 10, 4)
    b = run([tx("expense", 100, day=d1), tx("expense", 50, day=d1), tx("refund", 20, day=d2)])
    assert b.by_day[d1] == (Money(150, "KES"), 2)
    assert b.by_day[d2] == (Money(-20, "KES"), 1)


def test_unverified_records_are_counted_not_hidden():
    b = run([tx("expense", 100, verified=False), tx("expense", 100)])
    assert (b.included_count, b.unverified_count, b.total) == (2, 1, Money(200, "KES"))


def test_top_is_capped_ordered_and_ignores_refunds():
    records = [tx("expense", 100 * (i + 1), n=i + 1) for i in range(TOP_N + 3)]
    records.append(tx("refund", 99999, n=99))
    top = run(records).top
    assert len(top) == TOP_N
    assert [t.spend.amount_minor for t in top] == sorted(
        (t.spend.amount_minor for t in top), reverse=True
    )
    assert 99 not in {t.record.id.int for t in top}


def test_empty_input_is_a_true_zero_with_no_buckets():
    b = run([])
    assert b.total == Money(0, "KES")
    assert (b.by_merchant, dict(b.by_category), b.included_count) == ((), {}, 0)


record_strategy = st.builds(
    lambda kind, minor, acct, cat, merchant, day, hour, verified: tx(
        kind,
        minor,
        account=acct,
        category=cat,
        merchant=merchant,
        day=START + timedelta(days=day),
        at=None if hour is None else datetime(2026, 10, 10, hour, 15, tzinfo=UTC),
        verified=verified,
    ),
    st.sampled_from(list(SIGN)),
    st.integers(min_value=1, max_value=10_000_000),
    st.sampled_from([BANK, MPESA, SAVINGS]),
    st.sampled_from([None, FOOD, FUEL]),
    st.sampled_from([None, "A", "B", "C"]),
    st.integers(min_value=-2, max_value=32),  # some fall outside the period
    st.one_of(st.none(), st.integers(min_value=0, max_value=23)),
    st.booleans(),
)


@settings(max_examples=200, deadline=None)
@given(st.lists(record_strategy, max_size=60), st.booleans())
def test_every_dimension_sums_to_the_total_and_matches_cashflow(records, transfers):
    """SRS-DSH-02: merchant, category, account, day and hour each add up to the day total,
    and the total is the very same number the monthly report calls `expenses`."""
    policy = Policy(treat_transfers_as_spend=transfers)
    b = run(records, policy)
    flow = compute_cashflow(
        records, ACCOUNTS, policy, currency="KES", period_start=START, period_end=END
    )
    total = b.total.amount_minor
    assert total == flow.expenses.amount_minor
    assert sum(m.total.amount_minor for m in b.by_merchant) == total
    assert sum(v.amount_minor for v in b.by_category.values()) == total
    assert sum(v.amount_minor for v in b.by_account.values()) == total
    assert sum(v[0].amount_minor for v in b.by_day.values()) == total
    assert sum(v.amount_minor for v in b.by_hour.values()) + b.untimed.amount_minor == total
    assert {e.reason for e in b.exclusions} == {e.reason for e in flow.exclusions}
    assert len(b.exclusions) == len(flow.exclusions)

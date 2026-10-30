"""Dashboard on a seeded 12-month dataset (Phase 4 acceptance): ground truth, queries, latency.

The dataset is deterministic so totals can be recomputed independently in plain Python and
compared with what the API returns. Query counts are asserted exactly-ish (a regression that
adds a per-row query would multiply them); latency uses a generous bound so the test is stable
on slow machines, and the measured numbers are printed (run with `-s`) for the status report.
"""

from __future__ import annotations

import random
import statistics
import time
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.devices.models import Device
from apps.finance.models import Category, FinancialAccount, Merchant, Transaction
from apps.phonelog.models import CallRecord, CollectionSettings, MessageRecord
from tests.auth_fixtures import AuthedClient

pytestmark = pytest.mark.django_db

NAIROBI = ZoneInfo("Africa/Nairobi")
START = date(2025, 11, 1)
DAYS = 365
LAST = START + timedelta(days=DAYS - 1)
PER_DAY_TX = 40
PER_DAY_CALLS = 25
PER_DAY_MESSAGES = 25
LATENCY_BOUND_SECONDS = 2.0
BASE = "/api/v1/dashboard"
FULL_YEAR = {"range": "custom", "from": START.isoformat(), "to": LAST.isoformat()}
MONTH = {"range": "custom", "from": "2026-10-01", "to": "2026-10-31"}


def _local(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=NAIROBI).astimezone(UTC)


@pytest.fixture
def year(owner_a: User, client_a: AuthedClient):
    rng = random.Random(20261029)
    account = FinancialAccount.objects.create(
        owner=owner_a, name="Main", type="mpesa", currency="KES"
    )
    categories = [Category.objects.create(owner=owner_a, name=f"Cat {i}") for i in range(20)]
    merchants = [
        Merchant.objects.create(owner=owner_a, name=f"Shop {i}", normalized_name=f"shop {i}")
        for i in range(60)
    ]
    expected_total = 0
    expected_by_day: Counter[date] = Counter()
    txs: list[Transaction] = []
    for offset in range(DAYS):
        day = START + timedelta(days=offset)
        for n in range(PER_DAY_TX):
            minor = rng.randrange(1_000, 500_000)
            hour, minute = rng.randrange(0, 24), rng.randrange(0, 60)
            txs.append(
                Transaction(
                    owner=owner_a,
                    account=account,
                    posted_at=_local(day, hour, minute),
                    posted_on=day,
                    has_time=True,
                    amount_minor=-minor,
                    currency="KES",
                    kind="expense",
                    description=f"purchase {day} {n}",
                    merchant=rng.choice(merchants),
                    category=rng.choice([*categories, None]),
                    fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
                )
            )
            expected_total += minor
            expected_by_day[day] += minor
    Transaction.objects.bulk_create(txs, batch_size=2000)

    device: Device = client_a.device
    CollectionSettings.objects.create(
        owner=owner_a,
        device=device,
        enabled=True,
        last_upload_at=datetime(2026, 10, 28, 9, 0, tzinfo=UTC),
    )
    directions = ["incoming", "outgoing", "missed", "rejected"]
    calls: list[CallRecord] = []
    msgs: list[MessageRecord] = []
    calls_by_direction: Counter[str] = Counter()
    for offset in range(DAYS):
        day = START + timedelta(days=offset)
        for n in range(PER_DAY_CALLS):
            direction = rng.choice(directions)
            calls_by_direction[direction] += 1
            calls.append(
                CallRecord(
                    owner=owner_a,
                    device=device,
                    source_record_id=f"c{offset}-{n}",
                    direction=direction,
                    started_at=_local(day, rng.randrange(24), rng.randrange(60)),
                    duration_seconds=rng.randrange(0, 900),
                )
            )
        for n in range(PER_DAY_MESSAGES):
            msgs.append(
                MessageRecord(
                    owner=owner_a,
                    device=device,
                    source_record_id=f"m{offset}-{n}",
                    direction=rng.choice(["incoming", "outgoing"]),
                    occurred_at=_local(day, rng.randrange(24), rng.randrange(60)),
                    length_chars=40,
                )
            )
    CallRecord.objects.bulk_create(calls, batch_size=2000)
    MessageRecord.objects.bulk_create(msgs, batch_size=2000)
    return {
        "total": expected_total,
        "by_day": expected_by_day,
        "calls_by_direction": calls_by_direction,
        "tx_count": len(txs),
    }


def _measure(client: AuthedClient, path: str, params: dict[str, str]) -> tuple[float, int, dict]:
    cache.clear()  # measure the real work, not the cache
    with CaptureQueriesContext(connection) as queries:
        started = time.perf_counter()
        response = client.get(f"{BASE}/{path}", params)
        elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.content
    return elapsed, len(queries), response.json()


# (card, params, query budget). Budgets include authentication (1) and must not scale with rows.
BUDGETS = [
    ("summary", MONTH, 14),
    ("spending", FULL_YEAR, 8),
    ("calls", FULL_YEAR, 4),
    ("messages", FULL_YEAR, 4),
]


def test_twelve_month_dataset_ground_truth_queries_latency_and_cache(client_a, year):
    """One test on purpose: seeding 14,600 transactions and 18,250 phone rows is the slow part."""
    # --- ground truth: the API equals totals computed independently in plain Python
    _, _, body = _measure(client_a, "spending", FULL_YEAR)
    assert body["total"]["amount_minor"] == year["total"]
    assert body["included_count"] == year["tx_count"] == DAYS * PER_DAY_TX
    by_day = {d["date"]: d["total"]["amount_minor"] for d in body["by_day"]}
    assert by_day == {d.isoformat(): v for d, v in year["by_day"].items()}
    for key in ("by_merchant", "by_category", "by_account", "by_day"):
        assert sum(r["total"]["amount_minor"] for r in body[key]) == year["total"], key

    _, _, calls = _measure(client_a, "calls", FULL_YEAR)
    for api_key, direction in (
        ("received", "incoming"),
        ("missed", "missed"),
        ("outgoing", "outgoing"),
        ("rejected", "rejected"),
    ):
        assert calls[api_key] == year["calls_by_direction"][direction], api_key
    assert len(calls["by_day"]) == DAYS

    # --- query count is constant and latency bounded
    for path, params, max_queries in BUDGETS:
        samples = [_measure(client_a, path, params) for _ in range(3)]
        seconds = [s[0] for s in samples]
        queries = {s[1] for s in samples}
        print(
            f"[perf] {path:9s} queries={sorted(queries)} "
            f"median={statistics.median(seconds) * 1000:.0f} ms max={max(seconds) * 1000:.0f} ms"
        )
        assert max(queries) <= max_queries, f"{path} used {max(queries)} queries"
        assert max(seconds) < LATENCY_BOUND_SECONDS, f"{path} took {max(seconds):.2f}s"

    # --- a cache hit costs authentication only
    cache.clear()
    miss_started = time.perf_counter()
    client_a.get(f"{BASE}/spending", FULL_YEAR)
    miss = time.perf_counter() - miss_started
    with CaptureQueriesContext(connection) as hit_queries:
        hit_started = time.perf_counter()
        client_a.get(f"{BASE}/spending", FULL_YEAR)
        hit = time.perf_counter() - hit_started
    print(f"[perf] spending cache miss={miss * 1000:.0f} ms hit={hit * 1000:.0f} ms")
    assert len(hit_queries) <= 2
    assert hit < miss

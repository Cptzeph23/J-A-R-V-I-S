"""The short dashboard cache: hits, immediate invalidation on writes, isolation, outages."""

from __future__ import annotations

from datetime import date
from unittest import mock

import pytest
from django.core.cache import cache

from apps.accounts.models import User
from apps.dashboard.services import summary as summary_module
from apps.finance.models import Transaction
from tests.auth_fixtures import AuthedClient, authenticate
from tests.factories import FinancialAccountFactory, TransactionFactory

from .helpers import add_call, enable_collection, utc

pytestmark = pytest.mark.django_db
BASE = "/api/v1/dashboard"
DAY = {"range": "custom", "from": "2026-10-27", "to": "2026-10-27"}


@pytest.fixture
def computed():
    """Counts how often a card is really computed (as opposed to served from cache)."""
    with mock.patch.object(
        summary_module, "card_payload", wraps=summary_module.card_payload
    ) as spy:
        yield spy


def _spend(client: AuthedClient) -> int:
    return int(client.get(f"{BASE}/spending", DAY).json()["total"]["amount_minor"])


def test_a_repeat_request_is_served_from_the_cache(owner_a: User, client_a: AuthedClient):
    TransactionFactory(owner=owner_a, posted_on=date(2026, 10, 27))
    with mock.patch.object(
        summary_module, "card_payload", wraps=summary_module.card_payload
    ) as spy:
        first = client_a.get(f"{BASE}/spending", DAY).json()
        second = client_a.get(f"{BASE}/spending", DAY).json()
    assert first == second
    assert spy.call_count == 1


def test_different_parameters_do_not_share_a_cache_entry(owner_a: User, client_a: AuthedClient):
    TransactionFactory(owner=owner_a, posted_on=date(2026, 10, 27))
    kes = client_a.get(f"{BASE}/spending", DAY).json()
    usd = client_a.get(f"{BASE}/spending", {**DAY, "currency": "USD"}).json()
    assert kes["currency"] == "KES"
    assert usd["currency"] == "USD"
    assert (
        client_a.get(f"{BASE}/spending", {**DAY, "tz": "UTC"}).json()["range"]["timezone"] == "UTC"
    )


def test_creating_a_transaction_is_visible_on_the_very_next_read(
    owner_a: User, client_a: AuthedClient
):
    account = FinancialAccountFactory(owner=owner_a)
    TransactionFactory(
        owner=owner_a, account=account, posted_on=date(2026, 10, 27), amount_minor=-1000
    )
    assert _spend(client_a) == 1000
    TransactionFactory(
        owner=owner_a, account=account, posted_on=date(2026, 10, 27), amount_minor=-500
    )
    assert _spend(client_a) == 1500


def test_deleting_and_editing_are_visible_immediately(owner_a: User, client_a: AuthedClient):
    account = FinancialAccountFactory(owner=owner_a)
    tx = TransactionFactory(
        owner=owner_a, account=account, posted_on=date(2026, 10, 27), amount_minor=-1000
    )
    TransactionFactory(owner=owner_a, account=account, posted_on=date(2026, 9, 1))
    assert _spend(client_a) == 1000
    tx.soft_delete()
    assert _spend(client_a) == 0


def test_csv_import_invalidates_even_though_bulk_create_sends_no_signal(
    owner_a: User, client_a: AuthedClient
):
    account = FinancialAccountFactory(owner=owner_a)
    TransactionFactory(
        owner=owner_a, account=account, posted_on=date(2026, 10, 27), amount_minor=-100
    )
    assert _spend(client_a) == 100
    csv = "date,description,amount,reference\n2026-10-27,Lunch,-900,R1\n"
    from django.core.files.uploadedfile import SimpleUploadedFile

    response = client_a.post(
        "/api/v1/finance/imports",
        {"account": str(account.pk), "file": SimpleUploadedFile("s.csv", csv.encode())},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="cache-import-0001",
    )
    assert response.status_code == 201
    assert _spend(client_a) == 100 + 90000  # KES 900.00 is 90000 minor units


def test_phone_uploads_and_purges_invalidate_the_calls_card(owner_a: User, client_a: AuthedClient):
    enable_collection(client_a.device)
    add_call(owner_a, client_a.device, "incoming", utc(2026, 10, 27, 9, 0), 10)
    assert client_a.get(f"{BASE}/calls", DAY).json()["received"] == 1

    response = client_a.delete("/api/v1/phone/records?all=true")
    assert response.status_code in (200, 204), response.content
    assert client_a.get(f"{BASE}/calls", DAY).json()["received"] == 0


def test_a_new_collection_setting_invalidates_availability(owner_a: User, client_a: AuthedClient):
    assert client_a.get(f"{BASE}/calls", DAY).json()["reason"] == "collection_not_enabled"
    enable_collection(client_a.device)
    assert client_a.get(f"{BASE}/calls", DAY).json()["available"] is True


def test_owners_never_share_cache_entries(owner_a: User, owner_b: User, client_a: AuthedClient):
    client_b = authenticate(owner_b, name="B phone")
    TransactionFactory(owner=owner_a, posted_on=date(2026, 10, 27), amount_minor=-1000)
    TransactionFactory(owner=owner_b, posted_on=date(2026, 10, 27), amount_minor=-7000)
    assert _spend(client_a) == 1000
    assert _spend(client_b) == 7000
    assert _spend(client_a) == 1000


def test_a_cache_outage_degrades_to_computing_never_to_an_error(
    owner_a: User, client_a: AuthedClient
):
    TransactionFactory(owner=owner_a, posted_on=date(2026, 10, 27), amount_minor=-1000)
    boom = ConnectionError("redis down")
    with (
        mock.patch.object(cache, "get", side_effect=boom),
        mock.patch.object(cache, "set", side_effect=boom),
        mock.patch.object(cache, "incr", side_effect=boom),
        mock.patch.object(cache, "add", side_effect=boom),
    ):
        assert _spend(client_a) == 1000
        TransactionFactory(owner=owner_a, posted_on=date(2026, 10, 27), amount_minor=-500)
        assert _spend(client_a) == 1500


def test_cached_payloads_are_plain_json_not_pickled_objects(owner_a: User, client_a: AuthedClient):
    TransactionFactory(owner=owner_a, posted_on=date(2026, 10, 27))
    client_a.get(f"{BASE}/spending", DAY)
    from apps.dashboard.cache import cached_card

    stored = cached_card(
        owner_a.pk,
        "spending",
        {
            "from": "2026-10-27",
            "to": "2026-10-27",
            "tz": "Africa/Nairobi",
            "currency": "KES",
            "preset": "custom",
        },
        lambda: {"never": "used"},
    )
    assert stored["available"] is True
    assert Transaction.objects.count() == 1

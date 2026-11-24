from __future__ import annotations

from datetime import date

import pytest

from apps.finance.models import Budget
from tests.advisor_db import category, pinned_now, spend  # noqa: F401

BASE = "/api/v1/finance/budgets"
pytestmark = pytest.mark.django_db


def test_create_list_update_delete_budget(client_a, owner_a):
    food = category(owner_a, "Food")
    created = client_a.post(BASE, {"category": str(food.pk), "limit_minor": 1_000_000})
    assert created.status_code == 201
    body = created.json()
    assert body["limit"] == {"amount_minor": 1_000_000, "currency": owner_a.default_currency}
    assert body["category"] == str(food.pk)

    assert [b["id"] for b in client_a.get(BASE).json()["results"]] == [body["id"]]
    patched = client_a.patch(f"{BASE}/{body['id']}", {"limit_minor": 750_000})
    assert patched.status_code == 200
    assert patched.json()["limit"]["amount_minor"] == 750_000
    assert client_a.delete(f"{BASE}/{body['id']}").status_code == 204
    assert Budget.objects.count() == 0


def test_one_budget_per_category_and_one_overall(client_a, owner_a):
    food = category(owner_a, "Food")
    assert client_a.post(BASE, {"category": str(food.pk), "limit_minor": 100}).status_code == 201
    dup = client_a.post(BASE, {"category": str(food.pk), "limit_minor": 200})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "conflict"
    assert client_a.post(BASE, {"limit_minor": 100}).status_code == 201
    assert client_a.post(BASE, {"limit_minor": 100}).status_code == 409


@pytest.mark.parametrize("limit", [0, -5, "abc", None])
def test_limit_must_be_a_positive_integer(client_a, limit):
    assert client_a.post(BASE, {"limit_minor": limit}).status_code == 400


def test_a_foreign_category_cannot_be_budgeted(client_a, owner_b):
    theirs = category(owner_b, "Theirs")
    response = client_a.post(BASE, {"category": str(theirs.pk), "limit_minor": 100})
    assert response.status_code in (400, 404)
    assert Budget.objects.count() == 0


def test_budget_status_matches_spending(client_a, owner_a, pinned_now):  # noqa: F811
    food = category(owner_a, "Food")
    spend(owner_a, date(2026, 11, 2), 300_000, category=food)
    spend(owner_a, date(2026, 11, 3), 900_000)
    client_a.post(BASE, {"category": str(food.pk), "limit_minor": 1_000_000})
    client_a.post(BASE, {"limit_minor": 2_000_000})
    body = client_a.get(f"{BASE}/status?month=2026-11").json()
    rows = {b["label"]: b for b in body["budgets"]}
    assert rows["Food"]["spent"]["amount_minor"] == 300_000
    assert rows["Food"]["state"] == "ok"
    assert rows["Overall spending"]["spent"]["amount_minor"] == 1_200_000
    # 20 days into a 30-day month: 1.2m x 30 / 20 = 1.8m, under the 2m limit.
    assert rows["Overall spending"]["projected"]["amount_minor"] == 1_800_000
    assert body["assumptions"]


def test_status_rejects_a_bad_month(client_a):
    assert client_a.get(f"{BASE}/status?month=2026-13").status_code == 400


def test_budgets_require_authentication(db):
    from rest_framework.test import APIClient

    assert APIClient().get(BASE).status_code in (401, 403)

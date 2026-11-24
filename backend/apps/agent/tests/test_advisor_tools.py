"""Advisor tools through the orchestrator: read-only, default-granted, results untrusted."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from apps.advisor.services import recommendations
from apps.agent.llm.fake import call, say
from apps.agent.tools.base import DEFAULT_PERMISSIONS
from apps.agent.tools.registry import default_registry, invariant_violations
from apps.approvals.models import Approval
from tests.advisor_db import NOW, category, spend, subscription
from tests.agent_fixtures import drive, start

pytestmark = pytest.mark.django_db

ADVISOR_TOOLS = {
    "advisor_recurring_charges",
    "advisor_unusual_spending",
    "advisor_budget_status",
    "advisor_list_recommendations",
    "advisor_run_scenario",
}


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr("apps.agent.tools.advisor_tools.timezone", SimpleNamespace(now=lambda: NOW))


def result_text(llm) -> str:
    return str(llm.requests[1].messages[-1].tool_results[0].content)


def test_registry_is_valid_and_every_advisor_tool_is_read_only_and_default_granted():
    registry = default_registry()
    assert invariant_violations(registry) == []
    tools = {t.name: t for t in registry if t.name in ADVISOR_TOOLS}
    assert set(tools) == ADVISOR_TOOLS
    for tool in tools.values():
        assert not tool.mutating
        assert tool.permission in DEFAULT_PERMISSIONS
    # The owner decides on recommendations in the app; the assistant cannot.
    assert not [t.name for t in registry if "recommend" in t.name and t.mutating]


def test_recurring_tool_returns_the_charge_as_untrusted_data(owner_a):
    subscription(owner_a, "Netflix", 120_000)
    wf = start(owner_a)
    llm = drive(wf, [call("advisor_recurring_charges", {}), say("ok")])
    assert wf.state == "succeeded"
    assert wf.tainted is True
    text = result_text(llm)
    assert "<untrusted" in text
    assert "Netflix" in text


def test_budget_status_tool_reports_progress(owner_a):
    from apps.finance.services import budgets

    food = category(owner_a, "Food")
    spend(owner_a, date(2026, 11, 2), 300_000, category=food)
    budgets.create_budget(owner_a, category_id=food.pk, limit_minor=1_000_000, currency="KES")
    llm = drive(start(owner_a), [call("advisor_budget_status", {"month": "2026-11"}), say("ok")])
    assert "300000" in result_text(llm)


def test_scenario_tool_needs_no_approval_and_stores_nothing(owner_a):
    food = category(owner_a, "Food")
    for m in (8, 9, 10):
        spend(owner_a, date(2026, m, 1), 1_000_000, kind="income")
        spend(owner_a, date(2026, m, 10), 400_000, category=food)
    args = {
        "adjustments": [{"kind": "reduce_category", "category_id": str(food.pk), "percent": 25}]
    }
    wf = start(owner_a)
    llm = drive(wf, [call("advisor_run_scenario", args), say("ok")])
    assert wf.state == "succeeded"
    assert Approval.objects.count() == 0
    assert "100000" in result_text(llm)


def test_scenario_tool_refuses_another_owners_category(owner_a, owner_b):
    theirs = category(owner_b, "Theirs")
    args = {
        "adjustments": [{"kind": "reduce_category", "category_id": str(theirs.pk), "percent": 10}]
    }
    wf = start(owner_a)
    llm = drive(wf, [call("advisor_run_scenario", args), say("ok")])
    assert "unknown category" in result_text(llm)


def test_scenario_tool_rejects_malformed_input_before_running(owner_a):
    wf = start(owner_a)
    drive(wf, [call("advisor_run_scenario", {"adjustments": []}), say("x")])
    step = wf.steps.get()
    assert step.state != "succeeded"


def test_recommendation_list_shows_only_my_recommendations(owner_a, owner_b):
    subscription(owner_a, "Netflix", 120_000)
    subscription(owner_b, "Spotify", 90_000)
    recommendations.refresh(owner_a, now=NOW)
    recommendations.refresh(owner_b, now=NOW)
    llm = drive(start(owner_a), [call("advisor_list_recommendations", {}), say("ok")])
    text = result_text(llm)
    assert "Netflix" in text
    assert "Spotify" not in text
    assert "not by the assistant" in text


def test_unusual_spending_tool_runs_with_no_data(owner_a):
    wf = start(owner_a)
    drive(wf, [call("advisor_unusual_spending", {"month": "2026-11"}), say("ok")])
    assert wf.state == "succeeded"

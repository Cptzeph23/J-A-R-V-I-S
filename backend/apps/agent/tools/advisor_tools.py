"""Advisor tools: read-only analysis over the owner's recorded finances.

None of these changes anything. Accepting or rejecting a recommendation is the owner's own
action in the app; the assistant can only read and explain. Merchant and category names
come from outside, so results that contain them are marked untrusted.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.utils import timezone
from pydantic import Field

from apps.advisor import presenters
from apps.advisor.models import Recommendation, RecommendationState
from apps.advisor.services import analysis, scenario
from apps.common.money import Money
from apps.finance.reports import InvalidMonthError
from apps.finance.services import budgets as budget_service
from apps.finance.services import records

from ..services.executor import ToolError
from .base import PERM_FINANCE_READ, Tool, ToolContext, ToolInput

MAX_RECOMMENDATIONS = 20


class NoInput(ToolInput):
    pass


class MonthInput(ToolInput):
    month: str | None = Field(default=None, description="YYYY-MM; default the current month")


class ListRecommendationsInput(ToolInput):
    state: str = Field(default="new", pattern="^(new|accepted|rejected|expired|all)$")


class AdjustmentInput(ToolInput):
    kind: str = Field(pattern="^(reduce_category|cancel_recurring|change_income|add_expense)$")
    name: str = Field(default="", max_length=80)
    category_id: str | None = None
    percent: float | None = Field(default=None, ge=-100, le=1000)
    amount_minor: int | None = Field(default=None, ge=-(10**12), le=10**12)


class ScenarioInput(ToolInput):
    adjustments: list[AdjustmentInput] = Field(min_length=1, max_length=10)
    horizon_months: int = Field(default=12, ge=1, le=60)


def _today(ctx: ToolContext) -> Any:
    return records.owner_today(ctx.owner, timezone.now())


def _recurring(ctx: ToolContext, args: NoInput) -> dict[str, Any]:
    report = analysis.recurring(ctx.owner, today=_today(ctx), currency=ctx.owner.default_currency)
    return presenters.recurring_dict(report)


def _month_start(ctx: ToolContext, month: str | None) -> Any:
    from apps.finance.reports import month_bounds

    try:
        return month_bounds(month or _today(ctx).strftime("%Y-%m"))[0]
    except InvalidMonthError as exc:
        raise ToolError("invalid_month", str(exc)) from exc


def _anomalies(ctx: ToolContext, args: MonthInput) -> dict[str, Any]:
    report = analysis.anomalies(
        ctx.owner, month=_month_start(ctx, args.month), currency=ctx.owner.default_currency
    )
    return presenters.anomalies_dict(report, records.category_names(ctx.owner))


def _budget_status(ctx: ToolContext, args: MonthInput) -> dict[str, Any]:
    month = _month_start(ctx, args.month).strftime("%Y-%m")
    report = budget_service.status(ctx.owner, month=month, today=_today(ctx))
    return presenters.budget_report_dict(report, budget_service.labels(ctx.owner))


def _recommendations(ctx: ToolContext, args: ListRecommendationsInput) -> dict[str, Any]:
    rows = Recommendation.objects.for_owner(ctx.owner).order_by("-created_at", "id")
    if args.state != "all":
        rows = rows.filter(state=args.state)
    return {
        "recommendations": [
            {
                "id": str(r.pk),
                "kind": r.kind,
                "title": r.title,
                "summary": r.summary,
                "state": r.state,
                "estimated_monthly": Money(r.estimated_monthly_minor, r.currency).to_dict(),
                "payload": r.payload,
                "outcome": r.outcome,
            }
            for r in rows[:MAX_RECOMMENDATIONS]
        ],
        "note": "Accepting or rejecting is done by the owner in the app, not by the assistant.",
        "open_states": [RecommendationState.NEW.value, RecommendationState.ACCEPTED.value],
    }


def _scenario(ctx: ToolContext, args: ScenarioInput) -> dict[str, Any]:
    items = [a.model_dump() for a in args.adjustments]
    for item in items:
        if item["category_id"] is not None:
            try:
                item["category_id"] = uuid.UUID(item["category_id"])
            except ValueError:
                raise ToolError("invalid_category", "category_id is not a valid id.") from None
    try:
        result = scenario.run(
            ctx.owner,
            adjustments=items,
            horizon_months=args.horizon_months,
            now=timezone.now(),
        )
    except Exception as exc:  # validation problems become a readable tool error
        detail = getattr(exc, "detail", None)
        raise ToolError("invalid_scenario", str(detail or exc)) from exc
    return scenario.result_dict(result)


TOOLS: list[Tool] = [
    Tool(
        name="advisor_recurring_charges",
        description="Charges that repeat at a steady interval, with evidence and monthly cost.",
        input_model=NoInput,
        handler=_recurring,
        permission=PERM_FINANCE_READ,
        untrusted_result=True,
    ),
    Tool(
        name="advisor_unusual_spending",
        description="Categories and purchases far above the owner's own history for a month.",
        input_model=MonthInput,
        handler=_anomalies,
        permission=PERM_FINANCE_READ,
        untrusted_result=True,
    ),
    Tool(
        name="advisor_budget_status",
        description="Budgets for a month: spent, remaining, share used, labelled projection.",
        input_model=MonthInput,
        handler=_budget_status,
        permission=PERM_FINANCE_READ,
    ),
    Tool(
        name="advisor_list_recommendations",
        description="Saved recommendations with their data, assumptions, impact, trade-offs, "
        "uncertainty and tracking. Read only.",
        input_model=ListRecommendationsInput,
        handler=_recommendations,
        permission=PERM_FINANCE_READ,
        untrusted_result=True,
    ),
    Tool(
        name="advisor_run_scenario",
        description="What-if arithmetic on the last complete months (reduce a category, cancel "
        "a recurring charge, change income, add an expense). Stores nothing.",
        input_model=ScenarioInput,
        handler=_scenario,
        permission=PERM_FINANCE_READ,
    ),
]

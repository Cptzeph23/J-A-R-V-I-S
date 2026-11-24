"""Run a what-if scenario against the owner's recorded history."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.common.money import Money
from apps.finance.models import Category
from apps.finance.services import records

from ..scenarios import Adjustment, ScenarioError, ScenarioResult, compute_scenario
from . import analysis


def run(
    owner: User, *, adjustments: list[dict[str, Any]], horizon_months: int, now: datetime
) -> ScenarioResult:
    today = records.owner_today(owner, now)
    currency = owner.default_currency
    names = records.category_names(owner)
    parsed: list[Adjustment] = []
    for index, item in enumerate(adjustments):
        category_id: uuid.UUID | None = item.get("category_id")
        if (
            category_id is not None
            and not Category.objects.for_owner(owner).filter(pk=category_id).exists()
        ):
            raise ValidationError({"adjustments": [f"Item {index}: unknown category."]})
        amount_minor = item.get("amount_minor")
        percent = item.get("percent")
        parsed.append(
            Adjustment(
                kind=item["kind"],
                label=str(item.get("name", ""))[:80],
                category_id=category_id,
                percent=Decimal(str(percent)) if percent is not None else None,
                amount=Money(int(amount_minor), currency) if amount_minor is not None else None,
            )
        )
    baseline = analysis.baseline(owner, today=today, currency=currency)
    try:
        return compute_scenario(
            baseline, parsed, horizon_months=horizon_months, category_names=names
        )
    except ScenarioError as exc:
        raise ValidationError({"adjustments": [str(exc)]}) from exc


def result_dict(result: ScenarioResult) -> dict[str, Any]:
    def position(p: Any) -> dict[str, Any]:
        return {
            "income": p.income.to_dict(),
            "expenses": p.expenses.to_dict(),
            "net": p.net.to_dict(),
            "savings_rate": str(p.savings_rate) if p.savings_rate is not None else None,
        }

    return {
        "currency": result.currency,
        "horizon_months": result.horizon_months,
        "before": position(result.before),
        "after": position(result.after),
        "monthly_change": result.monthly_change.to_dict(),
        "cumulative_change": result.cumulative_change.to_dict(),
        "effects": [
            {
                "label": e.label,
                "kind": e.kind,
                "net_change": e.net_change.to_dict(),
                "note": e.note,
            }
            for e in result.effects
        ],
        "baseline_months": result.baseline_months,
        "net_range": {
            "lowest": result.net_range[0].to_dict(),
            "highest": result.net_range[1].to_dict(),
        },
        "assumptions": list(result.assumptions),
        "warnings": list(result.warnings),
    }

"""Turn calculator results into JSON-safe dictionaries (shared by the API and agent tools)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from apps.finance.calculators.anomalies import AnomalyReport
from apps.finance.calculators.budgets import BudgetReport
from apps.finance.calculators.recurring import RecurringReport


def recurring_dict(report: RecurringReport) -> dict[str, Any]:
    return {
        "currency": report.currency,
        "as_of": report.as_of.isoformat(),
        "active_monthly_total": report.active_monthly_total.to_dict(),
        "charges": [
            {
                "merchant": c.merchant,
                "cadence": c.cadence,
                "amount_pattern": c.amount_pattern,
                "typical_amount": c.typical_amount.to_dict(),
                "monthly_equivalent": c.monthly_equivalent.to_dict(),
                "occurrences": c.occurrences,
                "first_seen": c.first_seen.isoformat(),
                "last_seen": c.last_seen.isoformat(),
                "next_expected": c.next_expected.isoformat(),
                "active": c.active,
                "confidence": c.confidence,
                "evidence": [
                    {"date": d.isoformat(), "amount": a.to_dict()}
                    for d, a in zip(c.evidence_dates, c.evidence_amounts, strict=True)
                ],
                "category_id": str(c.category_id) if c.category_id else None,
            }
            for c in report.charges
        ],
        "assumptions": list(report.assumptions),
    }


def anomalies_dict(
    report: AnomalyReport, category_names: Mapping[uuid.UUID, str]
) -> dict[str, Any]:
    def name(cid: uuid.UUID | None) -> str | None:
        return category_names.get(cid, "Unknown") if cid else None

    return {
        "currency": report.currency,
        "month": report.month,
        "categories": [
            {
                "category_id": str(a.category_id) if a.category_id else None,
                "category": name(a.category_id),
                "spent": a.spent.to_dict(),
                "usual": a.usual.to_dict(),
                "excess": a.excess.to_dict(),
                "ratio": str(a.ratio),
                "history_months": a.history_months,
            }
            for a in report.categories
        ],
        "purchases": [
            {
                "transaction_id": str(p.transaction_id),
                "category_id": str(p.category_id) if p.category_id else None,
                "category": name(p.category_id),
                "merchant": p.merchant,
                "posted_on": p.posted_on.isoformat(),
                "amount": p.amount.to_dict(),
                "usual": p.usual.to_dict(),
                "ratio": str(p.ratio),
                "history_purchases": p.history_purchases,
            }
            for p in report.purchases
        ],
        "insufficient_history": [
            {"category_id": str(c) if c else None, "category": name(c)}
            for c in report.insufficient_history
        ],
        "assumptions": list(report.assumptions),
    }


def budget_report_dict(report: BudgetReport, labels: Mapping[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "currency": report.currency,
        "month": report.month,
        "budgets": [
            {
                "budget_id": str(s.budget_id),
                "label": labels.get(s.budget_id, "Budget"),
                "limit": s.limit.to_dict(),
                "spent": s.spent.to_dict(),
                "remaining": s.remaining.to_dict(),
                "used_share": str(s.used_share),
                "state": s.state,
                "projected": s.projected.to_dict() if s.projected else None,
                "projected_share": str(s.projected_share)
                if s.projected_share is not None
                else None,
                "days_elapsed": s.days_elapsed,
                "days_in_month": s.days_in_month,
            }
            for s in report.statuses
        ],
        "assumptions": list(report.assumptions),
    }

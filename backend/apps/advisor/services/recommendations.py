"""Recommendation lifecycle: refresh, accept, reject and measure the outcome.

Nothing here moves money or contacts anyone. Accepting a recommendation only records the
owner's decision and starts a measurement window; the owner makes any real-world change.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.audit.models import AuditActor
from apps.audit.recorder import record
from apps.common.errors import ConflictError
from apps.finance.calculators.cashflow import TxRecord, compute_cashflow
from apps.finance.services import budgets as budget_service
from apps.finance.services import records as finance_records

from ..engine import RULES_VERSION, WINDOW_DAYS, Draft, EngineInput, generate, review_date
from ..models import Recommendation, RecommendationState, RejectReason
from . import analysis

SUPPRESS_REJECTED_DAYS = 90
LOW_PROGRESS = Decimal("0.5")
RESULT_MET, RESULT_PARTIAL, RESULT_NOT_MET, RESULT_INCONCLUSIVE = (
    "met",
    "partial",
    "not_met",
    "inconclusive",
)


def _inputs(owner: User, today: date) -> EngineInput:
    currency = owner.default_currency
    return EngineInput(
        currency=currency,
        as_of=today,
        baseline=analysis.baseline(owner, today=today, currency=currency),
        recurring=analysis.recurring(owner, today=today, currency=currency),
        anomalies=analysis.anomalies(owner, month=today, currency=currency),
        budgets=analysis.budget_histories(owner, today=today, currency=currency),
        category_names=finance_records.category_names(owner),
        essential_categories=analysis.essential_categories(owner),
    )


def preview(owner: User, *, now: datetime) -> list[Draft]:
    """What a refresh would propose, without storing anything."""
    return generate(_inputs(owner, finance_records.owner_today(owner, now)))


def refresh(owner: User, *, now: datetime) -> dict[str, int]:
    today = finance_records.owner_today(owner, now)
    drafts = generate(_inputs(owner, today))
    created = updated = expired = suppressed = 0
    cutoff = now - timedelta(days=SUPPRESS_REJECTED_DAYS)
    rejected_keys = set(
        Recommendation.objects.for_owner(owner)
        .filter(state=RecommendationState.REJECTED, decided_at__gte=cutoff)
        .values_list("dedupe_key", flat=True)
    )
    open_rows = {
        r.dedupe_key: r
        for r in Recommendation.objects.for_owner(owner).filter(
            state__in=[RecommendationState.NEW, RecommendationState.ACCEPTED]
        )
    }
    wanted: set[str] = set()
    for draft in drafts:
        wanted.add(draft.dedupe_key)
        if draft.dedupe_key in rejected_keys:
            suppressed += 1
            continue
        existing = open_rows.get(draft.dedupe_key)
        if existing is not None:
            if existing.state == RecommendationState.NEW:
                _apply(existing, draft)
                existing.save()
                updated += 1
            continue
        try:
            with transaction.atomic():
                Recommendation.objects.create(
                    owner=owner,
                    kind=draft.kind,
                    dedupe_key=draft.dedupe_key,
                    title=draft.title,
                    summary=draft.summary,
                    currency=draft.currency,
                    estimated_monthly_minor=draft.estimated_monthly_minor,
                    payload=draft.payload,
                    rules_version=RULES_VERSION,
                )
            created += 1
        except IntegrityError:  # a concurrent refresh created it first
            continue
    for key, row in open_rows.items():
        if key not in wanted and row.state == RecommendationState.NEW:
            row.state = RecommendationState.EXPIRED
            row.decided_at = now
            row.save(update_fields=["state", "decided_at", "updated_at"])
            expired += 1
    record(
        "advisor.refreshed",
        owner_id=owner.pk,
        detail={"created": created, "updated": updated, "expired": expired},
    )
    return {"created": created, "updated": updated, "expired": expired, "suppressed": suppressed}


def _apply(row: Recommendation, draft: Draft) -> None:
    row.title = draft.title
    row.summary = draft.summary
    row.estimated_monthly_minor = draft.estimated_monthly_minor
    row.payload = draft.payload
    row.rules_version = RULES_VERSION


def accept(owner: User, row: Recommendation, *, now: datetime) -> Recommendation:
    if row.state == RecommendationState.ACCEPTED:
        return row  # idempotent
    if row.state != RecommendationState.NEW:
        raise ConflictError("This recommendation can no longer be accepted.")
    today = finance_records.owner_today(owner, now)
    row.state = RecommendationState.ACCEPTED
    row.decided_at = now
    row.review_on = review_date(today)
    row.save(update_fields=["state", "decided_at", "review_on", "updated_at"])
    record(
        "advisor.recommendation_accepted",
        owner_id=owner.pk,
        target_type="recommendation",
        target_id=row.pk,
        detail={"kind": row.kind, "review_on": row.review_on.isoformat() if row.review_on else ""},
    )
    return row


def reject(owner: User, row: Recommendation, *, reason: str, now: datetime) -> Recommendation:
    if reason not in RejectReason.values:
        raise ValidationError({"reason": ["Unknown reason."]})
    if row.state == RecommendationState.REJECTED:
        return row  # idempotent
    if row.state not in (RecommendationState.NEW, RecommendationState.ACCEPTED):
        raise ConflictError("This recommendation can no longer be rejected.")
    row.state = RecommendationState.REJECTED
    row.decided_at = now
    row.reject_reason = reason
    row.save(update_fields=["state", "decided_at", "reject_reason", "updated_at"])
    record(
        "advisor.recommendation_rejected",
        owner_id=owner.pk,
        target_type="recommendation",
        target_id=row.pk,
        detail={"kind": row.kind, "reason": reason},
    )
    return row


def _scoped(rows: list[TxRecord], tracking: dict[str, Any], owner: User) -> list[TxRecord]:
    metric = tracking.get("metric")
    subject = tracking.get("subject") or {}
    if metric == "merchant_spend":
        name = str(subject.get("merchant", "")).casefold().strip()
        return [r for r in rows if (r.merchant or "").casefold().strip() == name]
    if metric == "category_spend":
        raw = subject.get("category_id")
        if not raw:
            return []
        ids = budget_service.descendants(owner, uuid.UUID(str(raw)))
        return [r for r in rows if r.category_id in ids]
    return rows  # overall_spend and net use every record


def measure(owner: User, row: Recommendation, *, now: datetime) -> dict[str, Any]:
    """Compare what actually happened in the tracking window with the baseline and target."""
    payload = row.payload if isinstance(row.payload, dict) else {}
    tracking: dict[str, Any] = payload.get("tracking", {})
    currency = str(tracking.get("currency", row.currency))
    decided = row.decided_at or now
    start = finance_records.owner_today(owner, decided)
    end = start + timedelta(days=int(tracking.get("window_days", WINDOW_DAYS)) - 1)
    rows = _scoped(finance_records.load_records(owner, start=start, end=end), tracking, owner)
    flow = compute_cashflow(
        rows,
        finance_records.load_accounts(owner),
        finance_records.load_policy(owner),
        currency=currency,
        period_start=start,
        period_end=end,
    )
    metric = tracking.get("metric")
    actual = flow.net.amount_minor if metric == "net" else flow.expenses.amount_minor
    baseline = int(tracking["baseline_minor"])
    target = int(tracking["target_minor"])
    outcome: dict[str, Any] = {
        "measured_on": finance_records.owner_today(owner, now).isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "metric": metric,
        "actual_minor": actual,
        "baseline_minor": baseline,
        "target_minor": target,
        "currency": currency,
        "transactions_considered": flow.included_count,
    }
    if flow.included_count == 0 and metric != "merchant_spend":
        outcome.update(
            result=RESULT_INCONCLUSIVE,
            note="No transactions were recorded in the window, so nothing can be concluded.",
        )
        return outcome
    if target == baseline:
        outcome.update(result=RESULT_INCONCLUSIVE, note="Baseline and target are the same.")
        return outcome
    progress = (Decimal(actual - baseline) / Decimal(target - baseline)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )
    if progress == 0:
        progress = Decimal("0.00")  # never show "-0.00"
    outcome["progress"] = str(progress)
    if progress >= 1:
        result = RESULT_MET
    elif progress >= LOW_PROGRESS:
        result = RESULT_PARTIAL
    else:
        result = RESULT_NOT_MET
    outcome["result"] = result
    outcome["note"] = (
        "Measured over the window after you accepted; other changes in your finances in the "
        "same period also affect the figure, so this shows what happened, not proof of cause."
    )
    return outcome


def evaluate_due(*, now: datetime) -> int:
    """Measure every accepted recommendation whose window has ended. Idempotent."""
    done = 0
    due = Recommendation.objects.filter(
        state=RecommendationState.ACCEPTED, outcome__isnull=True, review_on__isnull=False
    ).select_related("owner")
    for row in due:
        owner = row.owner
        if row.review_on is None or row.review_on > finance_records.owner_today(owner, now):
            continue
        with transaction.atomic():
            fresh = Recommendation.objects.select_for_update().get(pk=row.pk)
            if fresh.outcome is not None:
                continue
            fresh.outcome = measure(owner, fresh, now=now)
            fresh.save(update_fields=["outcome", "updated_at"])
            record(
                "advisor.outcome_measured",
                owner_id=owner.pk,
                actor=AuditActor.SYSTEM,
                target_type="recommendation",
                target_id=fresh.pk,
                detail={"result": fresh.outcome.get("result", "")},
            )
        done += 1
    return done

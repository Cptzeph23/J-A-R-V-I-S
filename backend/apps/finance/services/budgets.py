"""Budgets: create, change, remove and report on monthly spending limits."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.audit.recorder import record
from apps.common.errors import ConflictError
from apps.common.money import Money

from ..calculators.budgets import BudgetReport, BudgetSpec, evaluate_budgets
from ..models import Budget, Category
from ..reports import month_bounds
from . import records
from .lookups import resolve_owned

MAX_BUDGETS = 100


def descendants(owner: User, root: uuid.UUID) -> frozenset[uuid.UUID]:
    """The category and every sub-category beneath it."""
    children: dict[uuid.UUID | None, list[uuid.UUID]] = {}
    for pk, parent_id in Category.objects.for_owner(owner).values_list("pk", "parent_id"):
        children.setdefault(parent_id, []).append(pk)
    found = {root}
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return frozenset(found)


def create_budget(
    owner: User, *, category_id: uuid.UUID | None, limit_minor: int, currency: str | None
) -> Budget:
    if limit_minor <= 0:
        raise ValidationError({"limit_minor": ["The limit must be positive."]})
    if Budget.objects.for_owner(owner).count() >= MAX_BUDGETS:
        raise ValidationError({"detail": [f"At most {MAX_BUDGETS} budgets are allowed."]})
    category = resolve_owned(Category, owner, category_id, "category") if category_id else None
    try:
        with transaction.atomic():
            budget = Budget.objects.create(
                owner=owner,
                category=category,
                limit_minor=limit_minor,
                currency=currency or owner.default_currency,
            )
            record(
                "finance.budget_created",
                owner_id=owner.pk,
                target_type="budget",
                target_id=budget.pk,
            )
    except IntegrityError as exc:
        raise ConflictError("A budget already exists for this category.") from exc
    return budget


def update_budget(owner: User, budget: Budget, *, limit_minor: int) -> Budget:
    if limit_minor <= 0:
        raise ValidationError({"limit_minor": ["The limit must be positive."]})
    budget.limit_minor = limit_minor
    budget.save(update_fields=["limit_minor", "updated_at"])
    record("finance.budget_updated", owner_id=owner.pk, target_type="budget", target_id=budget.pk)
    return budget


def delete_budget(owner: User, budget: Budget) -> None:
    pk = budget.pk
    budget.delete()
    record("finance.budget_deleted", owner_id=owner.pk, target_type="budget", target_id=pk)


def specs_for(owner: User, *, currency: str) -> list[BudgetSpec]:
    specs: list[BudgetSpec] = []
    for budget in Budget.objects.for_owner(owner).filter(currency=currency).order_by("id"):
        ids = descendants(owner, budget.category_id) if budget.category_id else None
        specs.append(
            BudgetSpec(
                id=budget.pk, limit=Money(budget.limit_minor, budget.currency), category_ids=ids
            )
        )
    return specs


def status(owner: User, *, month: str, today: date, currency: str | None = None) -> BudgetReport:
    start, end = month_bounds(month)
    chosen = currency or owner.default_currency
    return evaluate_budgets(
        specs_for(owner, currency=chosen),
        records.load_records(owner, start=start, end=end),
        records.load_accounts(owner),
        records.load_policy(owner),
        currency=chosen,
        month_start=start,
        today=today,
    )


def labels(owner: User) -> dict[uuid.UUID, str]:
    names = records.category_names(owner)
    result: dict[uuid.UUID, str] = {}
    for pk, category_id in Budget.objects.for_owner(owner).values_list("pk", "category_id"):
        result[pk] = names.get(category_id, "Category") if category_id else "Overall spending"
    return result


def as_dict(budget: Budget) -> dict[str, Any]:
    return {
        "id": str(budget.pk),
        "category_id": str(budget.category_id) if budget.category_id else None,
        "limit": budget.limit.to_dict(),
    }

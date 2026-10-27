"""Categories, categorization rules, merchants and the accounting policy."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.audit.recorder import record
from apps.common.errors import ConflictError

from ..domain import normalize_merchant_name, normalize_text
from ..models import AccountingPolicy, Category, CategoryRule, Merchant
from .lookups import resolve_owned

MAX_PARENT_DEPTH = 10
MAX_PATTERN_LENGTH = 200


def _check_parent(owner: User, category: Category | None, parent_id: uuid.UUID) -> Category:
    parent = resolve_owned(Category, owner, parent_id, "parent")
    if category is not None:
        cursor: Category | None = parent
        for _ in range(MAX_PARENT_DEPTH):
            if cursor is None:
                break
            if cursor.pk == category.pk:
                raise ValidationError({"parent": ["A category cannot be inside itself."]})
            cursor = cursor.parent
    return parent


def create_category(
    owner: User,
    *,
    name: str,
    parent_id: uuid.UUID | None = None,
    essential: bool = False,
) -> Category:
    parent = _check_parent(owner, None, parent_id) if parent_id else None
    try:
        with transaction.atomic():
            category = Category.objects.create(
                owner=owner, name=name, parent=parent, essential=essential
            )
            record(
                "finance.category_created",
                owner_id=owner.pk,
                target_type="category",
                target_id=category.pk,
            )
    except IntegrityError as exc:
        raise ConflictError("A category with this name already exists.") from exc
    return category


def update_category(
    owner: User,
    category: Category,
    *,
    name: str | None = None,
    essential: bool | None = None,
    parent_id: uuid.UUID | None = None,
    clear_parent: bool = False,
) -> Category:
    changed: list[str] = []
    if name is not None:
        category.name = name
        changed.append("name")
    if essential is not None:
        category.essential = essential
        changed.append("essential")
    if clear_parent:
        category.parent = None
        changed.append("parent")
    elif parent_id is not None:
        category.parent = _check_parent(owner, category, parent_id)
        changed.append("parent")
    if not changed:
        return category
    try:
        with transaction.atomic():
            category.save(update_fields=[*changed, "updated_at"])
            record(
                "finance.category_updated",
                owner_id=owner.pk,
                target_type="category",
                target_id=category.pk,
                detail={"fields": sorted(changed)},
            )
    except IntegrityError as exc:
        raise ConflictError("A category with this name already exists.") from exc
    return category


def create_rule(
    owner: User,
    *,
    match_type: str,
    pattern: str,
    category_id: uuid.UUID,
    priority: int = 100,
) -> CategoryRule:
    category = resolve_owned(Category, owner, category_id, "category")
    normalized = normalize_text(pattern)
    if not normalized:
        raise ValidationError({"pattern": ["Pattern must not be empty."]})
    if len(normalized) > MAX_PATTERN_LENGTH:
        raise ValidationError({"pattern": [f"At most {MAX_PATTERN_LENGTH} characters."]})
    try:
        with transaction.atomic():
            rule = CategoryRule.objects.create(
                owner=owner,
                match_type=match_type,
                pattern=normalized,
                category=category,
                priority=priority,
            )
            record(
                "finance.rule_created",
                owner_id=owner.pk,
                target_type="category_rule",
                target_id=rule.pk,
            )
    except IntegrityError as exc:
        raise ConflictError("A rule with this match type and pattern already exists.") from exc
    return rule


def delete_rule(rule: CategoryRule) -> None:
    with transaction.atomic():
        rule_id, owner_id = rule.pk, rule.owner_id
        rule.delete()
        record(
            "finance.rule_deleted",
            owner_id=owner_id,
            target_type="category_rule",
            target_id=rule_id,
        )


def ensure_merchants(owner: User, names: Iterable[str]) -> dict[str, Merchant]:
    """Return {normalized_name: Merchant}, creating any that do not exist yet.

    Race-safe: creation uses ON CONFLICT DO NOTHING and is followed by a re-read.
    """
    display: dict[str, str] = {}
    for raw in names:
        cleaned = " ".join(raw.split())
        if cleaned:
            display.setdefault(normalize_merchant_name(cleaned), cleaned)
    if not display:
        return {}
    found = {
        m.normalized_name: m
        for m in Merchant.objects.for_owner(owner).filter(normalized_name__in=list(display))
    }
    missing = [key for key in display if key not in found]
    if missing:
        Merchant.objects.bulk_create(
            [Merchant(owner=owner, name=display[key], normalized_name=key) for key in missing],
            ignore_conflicts=True,
        )
        found = {
            m.normalized_name: m
            for m in Merchant.objects.for_owner(owner).filter(normalized_name__in=list(display))
        }
    return found


def get_policy(owner: User) -> AccountingPolicy:
    policy, _ = AccountingPolicy.objects.get_or_create(owner=owner)
    return policy


def set_policy(owner: User, *, treat_transfers_as_spend: bool) -> AccountingPolicy:
    with transaction.atomic():
        policy = get_policy(owner)
        if policy.treat_transfers_as_spend != treat_transfers_as_spend:
            policy.treat_transfers_as_spend = treat_transfers_as_spend
            policy.save(update_fields=["treat_transfers_as_spend", "updated_at"])
            record(
                "finance.policy_changed",
                owner_id=owner.pk,
                target_type="accounting_policy",
                target_id=policy.pk,
                detail={"treat_transfers_as_spend": treat_transfers_as_spend},
            )
    return policy

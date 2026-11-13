"""Per-owner tool permissions. Reads are on by default; writes are off until granted."""

from __future__ import annotations

from collections.abc import Iterable

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.audit.recorder import record

from ..models import AgentSettings
from ..tools.base import ALL_PERMISSIONS, DEFAULT_PERMISSIONS


def granted_permissions(owner: User) -> frozenset[str]:
    row = AgentSettings.objects.for_owner(owner).first()
    if row is None:
        return DEFAULT_PERMISSIONS
    # Unknown strings in storage (a removed permission) grant nothing.
    return frozenset(row.granted_permissions) & ALL_PERMISSIONS


def set_permissions(owner: User, requested: Iterable[str]) -> frozenset[str]:
    wanted = frozenset(requested)
    unknown = wanted - ALL_PERMISSIONS
    if unknown:
        raise ValidationError({"granted_permissions": [f"Unknown: {', '.join(sorted(unknown))}."]})
    with transaction.atomic():
        row, _ = AgentSettings.objects.select_for_update().get_or_create(owner=owner)
        before = frozenset(row.granted_permissions)
        if before != wanted:
            row.granted_permissions = sorted(wanted)
            row.save(update_fields=["granted_permissions", "updated_at"])
            record(
                "agent.permissions_changed",
                owner_id=owner.pk,
                target_type="agent_settings",
                target_id=row.pk,
                detail={"granted": sorted(wanted - before), "revoked": sorted(before - wanted)},
            )
    return wanted

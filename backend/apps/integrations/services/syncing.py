"""What every provider sync shares: a lease so two workers never sync one integration, and
how provider trouble is recorded (backoff with jitter, reconnect needed, error)."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta

from apps.audit.models import AuditActor
from apps.audit.recorder import record

from ..models import Integration, IntegrationStatus
from ..providers.base import GrantRevoked, ProviderError, ProviderRateLimited, ProviderUnavailable

LEASE = timedelta(minutes=5)
MAX_BACKOFF_SECONDS = 3600
ACTIVE = [IntegrationStatus.CONNECTED, IntegrationStatus.DEGRADED]


def backoff_seconds(failures: int, jitter_unit: float, retry_after: int = 0) -> int:
    """Exponential from 30s, capped, scaled by jitter in [0.5, 1.0]; honours Retry-After."""
    base = min(30 * (2 ** max(failures, 0)), MAX_BACKOFF_SECONDS)
    return max(retry_after, int(base * (0.5 + 0.5 * jitter_unit)))


def jitter() -> float:
    return secrets.randbelow(10_001) / 10_000


def claim(integration_id: uuid.UUID, kind: str, now: datetime) -> Integration | None:
    """Take the sync lease, or None when another run holds it."""
    base = Integration.objects.filter(pk=integration_id, kind=kind, status__in=ACTIVE)
    claimed = base.filter(sync_lease_until__isnull=True).update(sync_lease_until=now + LEASE) or (
        base.filter(sync_lease_until__lt=now).update(sync_lease_until=now + LEASE)
    )
    return Integration.objects.get(pk=integration_id) if claimed else None


def release(integration_id: uuid.UUID) -> None:
    Integration.objects.filter(pk=integration_id).update(sync_lease_until=None)


def settle_failure(integration: Integration, exc: ProviderError, now: datetime) -> str:
    """Record a provider failure and return the sync outcome name."""
    if isinstance(exc, GrantRevoked):
        Integration.objects.filter(pk=integration.pk).update(
            status=IntegrationStatus.REAUTH_REQUIRED, last_error="grant_revoked"
        )
        record(
            "integration.grant_lost",
            owner_id=integration.owner_id,
            actor=AuditActor.SYSTEM,
            target_type="integration",
            target_id=integration.pk,
        )
        return "reauth_required"
    if isinstance(exc, ProviderUnavailable):
        failures = integration.consecutive_failures + 1
        retry_after = exc.retry_after_seconds if isinstance(exc, ProviderRateLimited) else 0
        Integration.objects.filter(pk=integration.pk).update(
            status=IntegrationStatus.DEGRADED,
            consecutive_failures=failures,
            last_error=exc.code,
            next_sync_after=now
            + timedelta(seconds=backoff_seconds(failures, jitter(), retry_after)),
        )
        return "degraded"
    Integration.objects.filter(pk=integration.pk).update(
        status=IntegrationStatus.ERROR, last_error=exc.code
    )
    return "degraded"


def settle_success(integration: Integration, now: datetime, *, next_in_seconds: int) -> None:
    Integration.objects.filter(pk=integration.pk).update(
        status=IntegrationStatus.CONNECTED,
        consecutive_failures=0,
        last_error="",
        last_synced_at=now,
        next_sync_after=now + timedelta(seconds=next_in_seconds),
    )

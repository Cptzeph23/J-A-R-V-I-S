"""Valid access tokens on demand, and disconnecting."""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.accounts.models import User
from apps.audit.models import AuditActor
from apps.audit.recorder import record
from apps.common import secretbox

from ..models import Integration, IntegrationStatus, OAuthCredential
from ..providers.base import GrantRevoked, ProviderError
from ..providers.registry import get_oauth_provider
from .errors import IntegrationUnusableError
from .oauth import _purge_cached_data, store_tokens

logger = structlog.get_logger(__name__)


def access_token_for(integration: Integration, *, now: datetime | None = None) -> str:
    """A usable access token, refreshing under a row lock so parallel workers refresh once.

    Raises IntegrationUnusableError when there is nothing to refresh with or the provider says
    the grant is gone; the integration is then marked reauth_required and the tokens deleted.
    """
    moment = now or timezone.now()
    revoked = False
    token = ""  # nosec B105 - empty initial value, not a credential
    with transaction.atomic():
        cred = (
            OAuthCredential.objects.select_for_update()
            .filter(integration_id=integration.pk)
            .first()
        )
        if cred is None or not integration.is_active:
            raise IntegrationUnusableError()
        if not cred.expiring(moment):
            return secretbox.open_sealed(cred.access_token_sealed, f"oauth.access:{cred.pk}")
        refresh_token = secretbox.open_sealed(cred.refresh_token_sealed, f"oauth.refresh:{cred.pk}")
        try:
            fresh = get_oauth_provider(integration.provider).refresh(refresh_token)
        except GrantRevoked:
            revoked = True
            cred.delete()
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
        else:
            store_tokens(
                integration,
                fresh.access_token,
                fresh.refresh_token or refresh_token,
                fresh.expires_in,
                moment,
            )
            token = fresh.access_token
    if revoked:
        integration.refresh_from_db()
        raise IntegrationUnusableError()
    return token


def disconnect(owner: User, integration_id: uuid.UUID) -> Integration:
    """Revoke at the provider (best effort), then always delete the tokens and cached mail."""
    integration = Integration.objects.for_owner(owner).filter(pk=integration_id).first()
    if integration is None:
        raise NotFound()
    if integration.status == IntegrationStatus.REVOKED:
        return integration

    revoked_remotely = False
    cred = OAuthCredential.objects.filter(integration=integration).first()
    if cred is not None:
        try:
            token = secretbox.open_sealed(cred.refresh_token_sealed, f"oauth.refresh:{cred.pk}")
            get_oauth_provider(integration.provider).revoke(token)
            revoked_remotely = True
        except (ProviderError, secretbox.SecretBoxError) as exc:
            logger.warning("provider_revoke_failed", code=getattr(exc, "code", "secretbox"))

    with transaction.atomic():
        locked = Integration.objects.select_for_update().get(pk=integration.pk)
        OAuthCredential.objects.filter(integration=locked).delete()
        _purge_cached_data(locked)
        locked.status = IntegrationStatus.REVOKED
        locked.sync_cursor = ""
        locked.next_sync_after = None
        locked.sync_lease_until = None
        locked.last_error = ""
        locked.save()
        record(
            "integration.disconnected",
            owner_id=owner.pk,
            target_type="integration",
            target_id=locked.pk,
            detail={"revoked_at_provider": revoked_remotely},
        )
    return locked

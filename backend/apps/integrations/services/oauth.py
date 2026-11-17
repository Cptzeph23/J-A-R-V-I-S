"""Connect a provider account: authorization code flow with PKCE, single-use state."""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditActor
from apps.audit.recorder import record
from apps.common import secretbox

from ..models import Integration, IntegrationStatus, Kind, OAuthCredential, OAuthState, Provider
from ..providers.base import ProviderError, ProviderNotConfigured
from ..providers.google.calendar import SCOPE_CALENDAR_EVENTS
from ..providers.google.gmail import SCOPE_MAIL_READ
from ..providers.registry import get_calendar_provider, get_mail_provider, get_oauth_provider
from .errors import (
    InvalidOAuthStateError,
    OAuthDeniedError,
    ProviderNotConfiguredError,
    ScopeNotGrantedError,
)

logger = structlog.get_logger(__name__)

# The complete set of permissions the system ever requests (tests pin this: docs/07 T09).
SCOPES: dict[str, tuple[str, ...]] = {
    Kind.MAIL: (SCOPE_MAIL_READ,),
    Kind.CALENDAR: (SCOPE_CALENDAR_EVENTS,),
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def start_authorization(owner: User, *, provider: str, kind: str) -> str:
    if provider not in Provider.values or kind not in SCOPES:
        raise InvalidOAuthStateError()
    redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    row_id = uuid.uuid4()
    now = timezone.now()
    try:
        url = get_oauth_provider(provider).build_authorization_url(
            state=state,
            code_challenge=_challenge(verifier),
            scopes=SCOPES[kind],
            redirect_uri=redirect_uri,
        )
    except ProviderNotConfigured as exc:
        raise ProviderNotConfiguredError() from exc
    if not redirect_uri:
        raise ProviderNotConfiguredError()
    with transaction.atomic():
        OAuthState.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()
        OAuthState.objects.create(
            id=row_id,
            owner=owner,
            state_hash=_hash(state),
            provider=provider,
            kind=kind,
            scopes=list(SCOPES[kind]),
            code_verifier_sealed=secretbox.seal(verifier, f"oauth.verifier:{row_id}"),
            expires_at=now + timedelta(seconds=settings.OAUTH_STATE_TTL_SECONDS),
        )
        record(
            "integration.authorization_started",
            owner_id=owner.pk,
            target_type="integration",
            detail={"provider": provider, "kind": kind},
        )
    return url


def _consume(state: str) -> OAuthState:
    """Atomically mark the state used. A replayed or unknown state raises."""
    now = timezone.now()
    row = OAuthState.objects.filter(state_hash=_hash(state)).first()
    if row is None or not row.is_usable(now):
        raise InvalidOAuthStateError()
    claimed = OAuthState.objects.filter(pk=row.pk, consumed_at__isnull=True).update(consumed_at=now)
    if claimed != 1:
        raise InvalidOAuthStateError()
    return row


def complete_authorization(*, state: str, code: str, error: str = "") -> Integration:
    """Called from the provider redirect. The owner comes from the stored state."""
    row = _consume(state)
    owner = User.objects.get(pk=row.owner_id)
    if error or not code:
        record(
            "integration.authorization_denied",
            owner_id=owner.pk,
            actor=AuditActor.USER,
            target_type="integration",
            detail={"provider": row.provider, "reason": "provider_error" if error else "no_code"},
        )
        raise OAuthDeniedError()

    verifier = secretbox.open_sealed(row.code_verifier_sealed, f"oauth.verifier:{row.pk}")
    oauth = get_oauth_provider(row.provider)
    try:
        tokens = oauth.exchange_code(
            code=code, code_verifier=verifier, redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI
        )
        if not tokens.refresh_token:
            raise ProviderError("no refresh token issued")
        if not set(row.scopes) <= set(tokens.scopes):
            record(
                "integration.authorization_denied",
                owner_id=owner.pk,
                target_type="integration",
                detail={"provider": row.provider, "reason": "scope_not_granted"},
            )
            raise ScopeNotGrantedError()
        if row.kind == Kind.CALENDAR:
            account = get_calendar_provider(row.provider).account_email(tokens.access_token)
        else:
            account = get_mail_provider(row.provider).profile(tokens.access_token).email
    except ProviderNotConfigured as exc:
        raise ProviderNotConfiguredError() from exc
    except ProviderError as exc:
        logger.warning("oauth_exchange_failed", code=exc.code, provider=row.provider)
        raise OAuthDeniedError() from exc

    now = timezone.now()
    with transaction.atomic():
        integration, created = Integration.objects.select_for_update().get_or_create(
            owner=owner,
            provider=row.provider,
            kind=row.kind,
            defaults={"granted_scopes": list(tokens.scopes)},
        )
        was_revoked = integration.status == IntegrationStatus.REVOKED
        switched = bool(integration.external_account_id) and (
            integration.external_account_id != account
        )
        integration.status = IntegrationStatus.CONNECTED
        integration.granted_scopes = list(tokens.scopes)
        integration.external_account_id = account
        integration.consecutive_failures = 0
        integration.last_error = ""
        integration.next_sync_after = now
        if switched or created or was_revoked:
            integration.sync_cursor = ""
        if switched:
            _purge_cached_data(integration)
        integration.save()
        store_tokens(integration, tokens.access_token, tokens.refresh_token, tokens.expires_in, now)
        record(
            "integration.connected",
            owner_id=owner.pk,
            target_type="integration",
            target_id=integration.pk,
            detail={"provider": row.provider, "kind": row.kind, "account_switched": switched},
        )
    from .dispatch import enqueue_sync_for

    enqueue_sync_for(integration)
    return integration


def store_tokens(
    integration: Integration, access: str, refresh: str, expires_in: int, now: object
) -> OAuthCredential:
    cred = OAuthCredential.objects.filter(integration=integration).first()
    cred_id = cred.pk if cred else uuid.uuid4()
    fields = {
        "access_token_sealed": secretbox.seal(access, f"oauth.access:{cred_id}"),
        "refresh_token_sealed": secretbox.seal(refresh, f"oauth.refresh:{cred_id}"),
        "expires_at": now + timedelta(seconds=expires_in),  # type: ignore[operator]
        "key_id": secretbox.active_key_id(),
    }
    if cred is None:
        return OAuthCredential.objects.create(
            id=cred_id, owner_id=integration.owner_id, integration=integration, **fields
        )
    for name, value in fields.items():
        setattr(cred, name, value)
    cred.save()
    return cred


def _purge_cached_data(integration: Integration) -> None:
    from apps.mailbox.models import MailMessage
    from apps.scheduling.models import CalendarEvent

    MailMessage.objects.filter(integration=integration).delete()
    CalendarEvent.objects.filter(integration=integration).delete()

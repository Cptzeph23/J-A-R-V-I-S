"""Connecting, choosing and disconnecting the Page."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditActor
from apps.audit.recorder import record
from apps.common import secretbox

from ..errors import (
    ConnectDeniedError,
    InvalidConnectStateError,
    NoPageAccessError,
    SocialNotConfiguredError,
    SocialNotConnectedError,
)
from ..models import AccountStatus, ConnectState, PostState, SocialAccount, SocialPost
from ..providers.base import NotConfigured, PageGrant, SocialProviderError
from ..providers.registry import get_provider

logger = structlog.get_logger(__name__)
PROVIDER = "facebook"
NO_TOKEN = ""  # nosec B105 - empty marker meaning "no token stored", not a credential
CANDIDATE_TTL = timedelta(minutes=15)
# States in which a post has not been published and may still be wanted.
OPEN_STATES = [PostState.DRAFT, PostState.APPROVED, PostState.QUEUED, PostState.FAILED]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def active_account(owner: User) -> SocialAccount:
    account = SocialAccount.objects.for_owner(owner).first()
    if account is None or not account.is_active:
        raise SocialNotConnectedError()
    return account


def token_of(account: SocialAccount) -> str:
    return secretbox.open_sealed(account.token_sealed, f"social.token:{account.pk}")


def start(owner: User) -> str:
    if not settings.META_REDIRECT_URI:
        raise SocialNotConfiguredError()
    state = secrets.token_urlsafe(32)
    now = timezone.now()
    try:
        url = get_provider(PROVIDER).build_authorization_url(
            state=state, redirect_uri=settings.META_REDIRECT_URI
        )
    except NotConfigured as exc:
        raise SocialNotConfiguredError() from exc
    with transaction.atomic():
        ConnectState.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()
        ConnectState.objects.create(
            owner=owner,
            state_hash=_hash(state),
            expires_at=now + timedelta(seconds=settings.OAUTH_STATE_TTL_SECONDS),
        )
        record(
            "social.authorization_started",
            owner_id=owner.pk,
            target_type="social_account",
            detail={"provider": PROVIDER},
        )
    return url


def _consume(state: str, now: datetime) -> ConnectState:
    row = ConnectState.objects.filter(state_hash=_hash(state)).first()
    if row is None or row.consumed_at is not None or row.expires_at <= now:
        raise InvalidConnectStateError()
    claimed = ConnectState.objects.filter(pk=row.pk, consumed_at__isnull=True).update(
        consumed_at=now
    )
    if claimed != 1:
        raise InvalidConnectStateError()
    return row


def complete(*, state: str, code: str, error: str = "") -> tuple[str, SocialAccount | None]:
    """The provider redirect. Returns ("connected", account) or ("select_page", None)."""
    now = timezone.now()
    row = _consume(state, now)
    owner = User.objects.get(pk=row.owner_id)
    if error or not code:
        record(
            "social.authorization_denied",
            owner_id=owner.pk,
            actor=AuditActor.USER,
            target_type="social_account",
            detail={"reason": "provider_error" if error else "no_code"},
        )
        raise ConnectDeniedError()
    try:
        grants = get_provider(PROVIDER).exchange_code(
            code=code, redirect_uri=settings.META_REDIRECT_URI
        )
    except NotConfigured as exc:
        raise SocialNotConfiguredError() from exc
    except SocialProviderError as exc:
        logger.warning("social_exchange_failed", code=exc.code)
        raise ConnectDeniedError() from exc
    if not grants:
        raise NoPageAccessError()
    if len(grants) == 1:
        return "connected", _activate(owner, grants[0], now)
    payload = json.dumps([{"id": g.page_id, "name": g.name, "token": g.token} for g in grants])
    row.candidates_sealed = secretbox.seal(payload, f"social.candidates:{row.pk}")
    row.candidates_expire_at = now + CANDIDATE_TTL
    row.save(update_fields=["candidates_sealed", "candidates_expire_at", "updated_at"])
    return "select_page", None


def _pending(owner: User, now: datetime) -> ConnectState | None:
    return (
        ConnectState.objects.for_owner(owner)
        .filter(candidates_expire_at__gt=now)
        .exclude(candidates_sealed="")
        .order_by("-created_at")
        .first()
    )


def candidates(owner: User, now: datetime | None = None) -> list[dict[str, str]]:
    """Pages the owner may choose from. Names and ids only; tokens never leave the server."""
    row = _pending(owner, now or timezone.now())
    if row is None:
        return []
    items = json.loads(secretbox.open_sealed(row.candidates_sealed, f"social.candidates:{row.pk}"))
    return [{"page_id": i["id"], "name": i["name"]} for i in items]


def select(owner: User, page_id: str, now: datetime | None = None) -> SocialAccount:
    moment = now or timezone.now()
    row = _pending(owner, moment)
    if row is None:
        raise InvalidConnectStateError()
    items = json.loads(secretbox.open_sealed(row.candidates_sealed, f"social.candidates:{row.pk}"))
    chosen = next((i for i in items if i["id"] == page_id), None)
    if chosen is None:
        raise NoPageAccessError()
    with transaction.atomic():
        account = _activate(
            owner,
            PageGrant(page_id=chosen["id"], name=chosen["name"], token=chosen["token"]),
            moment,
        )
        ConnectState.objects.for_owner(owner).update(
            candidates_sealed="", candidates_expire_at=None
        )
    return account


def _activate(owner: User, grant: PageGrant, now: datetime) -> SocialAccount:
    with transaction.atomic():
        account = SocialAccount.objects.for_owner(owner).select_for_update().first()
        created = account is None
        if account is None:
            account = SocialAccount(id=uuid.uuid4(), owner=owner)
        switched = not created and account.page_id != grant.page_id
        account.provider = PROVIDER
        account.page_id = grant.page_id
        account.page_name = grant.name
        account.status = AccountStatus.CONNECTED
        account.token_sealed = secretbox.seal(grant.token, f"social.token:{account.pk}")
        account.key_id = secretbox.active_key_id()
        account.connected_at = now
        account.last_error = ""
        account.save()
        cancelled = 0
        if switched:  # approvals were for another Page: none of them may carry over
            cancelled = SocialPost.objects.filter(owner=owner, state__in=OPEN_STATES).update(
                state=PostState.CANCELLED, error_code="page_changed", updated_at=now
            )
        record(
            "social.connected",
            owner_id=owner.pk,
            target_type="social_account",
            target_id=account.pk,
            detail={"provider": PROVIDER, "page_switched": switched, "posts_cancelled": cancelled},
        )
    return account


def mark_reauth_required(account: SocialAccount, code: str = "token_rejected") -> None:
    SocialAccount.objects.filter(pk=account.pk).update(
        status=AccountStatus.REAUTH_REQUIRED,
        token_sealed=NO_TOKEN,
        last_error=code,
    )
    record(
        "social.grant_lost",
        owner_id=account.owner_id,
        actor=AuditActor.SYSTEM,
        target_type="social_account",
        target_id=account.pk,
    )
    from apps.events.services.ingest import safe_ingest

    safe_ingest(
        account.owner_id,
        "social",
        f"reauth:{account.pk}:{uuid.uuid4().hex[:8]}",
        "social.reauth_required",
        {},
    )


def disconnect(owner: User) -> SocialAccount | None:
    """Delete the stored token and cancel anything approved but not yet published.

    The Page token cannot be revoked at Meta from here (only the user token can, and it is not
    kept); the owner removes the app in Facebook's Business Integrations settings for that.
    """
    with transaction.atomic():
        account = SocialAccount.objects.for_owner(owner).select_for_update().first()
        if account is None:
            return None
        if account.status == AccountStatus.REVOKED:
            return account
        account.token_sealed = NO_TOKEN
        account.status = AccountStatus.REVOKED
        account.last_error = ""
        account.save()
        cancelled = SocialPost.objects.filter(
            owner=owner, state__in=[PostState.APPROVED, PostState.QUEUED]
        ).update(state=PostState.CANCELLED, error_code="account_disconnected")
        ConnectState.objects.for_owner(owner).update(
            candidates_sealed="", candidates_expire_at=None
        )
        record(
            "social.disconnected",
            owner_id=owner.pk,
            target_type="social_account",
            target_id=account.pk,
            detail={"posts_cancelled": cancelled},
        )
    return account

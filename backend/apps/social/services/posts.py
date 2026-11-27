"""The post lifecycle. The only place that changes a post's state.

draft -> approved -> queued -> publishing -> published | failed | uncertain
Editing an approved or failed post returns it to draft and clears the approval.

Duplicate protection (the Graph API offers none): a post is sent at most once per approval.
Only `queued` can be sent; sending takes a lease; anything that might have reached the platform
and has no confirmed answer becomes `uncertain`, which nothing retries automatically and which
the owner or a reconcile against the Page's own recent posts resolves.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.accounts.models import User
from apps.approvals.models import Approval
from apps.audit.models import AuditActor
from apps.audit.recorder import record
from apps.events.services.ingest import safe_ingest

from ..errors import (
    ApprovalExpiredError,
    ContentChangedError,
    DailyLimitError,
    NotApprovedError,
    SocialNotConnectedError,
    UncertainOutcomeError,
    WrongStateError,
)
from ..models import PostSource, PostState, SocialAccount, SocialPost
from ..providers.base import (
    OutcomeUnknown,
    PublishRejected,
    RateLimited,
    ReadFailed,
    TokenRejected,
)
from ..providers.registry import get_provider
from . import accounts, content

logger = structlog.get_logger(__name__)
EDITABLE = {PostState.DRAFT, PostState.APPROVED, PostState.FAILED}
CANCELLABLE = {PostState.DRAFT, PostState.APPROVED, PostState.QUEUED, PostState.FAILED}
RECONCILE_MARGIN = timedelta(minutes=5)
STUCK_QUEUE_SECONDS = 120


def _locked(owner: User, post_id: uuid.UUID) -> SocialPost:
    post = SocialPost.objects.for_owner(owner).select_for_update().filter(pk=post_id).first()
    if post is None:
        raise NotFound()
    return post


def _audit(post: SocialPost, action: str, actor: str = AuditActor.USER, **detail: object) -> None:
    record(
        action,
        owner_id=post.owner_id,
        actor=actor,
        target_type="social_post",
        target_id=post.pk,
        detail={"state": post.state, **detail},  # never the text
    )


def _account_of(post: SocialPost) -> SocialAccount | None:
    if post.account_id is None:
        return None
    return SocialAccount.objects.filter(pk=post.account_id).first()


def _intact(post: SocialPost) -> bool:
    """The stored content still hashes to what was shown and approved."""
    expected = content.content_hash(post.page_id, post.text, post.link)
    return post.content_sha256 == expected


# --- create and edit ------------------------------------------------------------------------


def create_draft(
    owner: User, *, text: str, link: str = "", source: str = PostSource.OWNER
) -> SocialPost:
    account = accounts.active_account(owner)
    clean, url = content.clean_text(text), content.clean_link(link)
    with transaction.atomic():
        post = SocialPost.objects.create(
            owner=owner,
            account=account,
            page_id=account.page_id,
            page_name=account.page_name,
            text=clean,
            link=url,
            content_sha256=content.content_hash(account.page_id, clean, url),
            source=source,
        )
        _audit(post, "social.post_created", source=source)
    return post


def update_draft(
    owner: User, post_id: uuid.UUID, *, text: str | None = None, link: str | None = None
) -> SocialPost:
    with transaction.atomic():
        post = _locked(owner, post_id)
        if post.state not in EDITABLE:
            raise WrongStateError("Only a draft, approved or failed post can be edited.")
        was = post.state
        new_text = content.clean_text(text) if text is not None else post.text
        new_link = content.clean_link(link) if link is not None else post.link
        post.text, post.link = new_text, new_link
        post.content_sha256 = content.content_hash(post.page_id, new_text, new_link)
        post.state = PostState.DRAFT
        post.approved_at = None
        post.approved_sha256 = ""
        post.approval_expires_at = None
        post.approved_via = ""
        post.error_code = ""
        post.save()
        _audit(post, "social.post_edited", previous_state=was)
    return post


def cancel(owner: User, post_id: uuid.UUID) -> SocialPost:
    with transaction.atomic():
        post = _locked(owner, post_id)
        if post.state == PostState.CANCELLED:
            return post
        if post.state not in CANCELLABLE:
            raise WrongStateError("This post can no longer be cancelled.")
        post.state = PostState.CANCELLED
        post.error_code = ""
        post.save()
        _audit(post, "social.post_cancelled")
    return post


# --- approval -------------------------------------------------------------------------------


def approve(
    owner: User,
    post_id: uuid.UUID,
    *,
    echoed_sha256: str,
    device_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> SocialPost:
    """The owner approves exactly what they were shown. The echoed hash is the proof."""
    moment = now or timezone.now()
    with transaction.atomic():
        post = _locked(owner, post_id)
        if post.state not in {PostState.DRAFT, PostState.APPROVED, PostState.FAILED}:
            raise WrongStateError("This post cannot be approved in its current state.")
        if echoed_sha256 != post.content_sha256 or not _intact(post):
            raise ContentChangedError()
        accounts.active_account(owner)
        post.state = PostState.APPROVED
        post.approved_at = moment
        post.approved_sha256 = post.content_sha256
        post.approval_expires_at = moment + timedelta(seconds=settings.SOCIAL_APPROVAL_TTL_SECONDS)
        post.approved_via = "owner_tap"
        post.error_code = ""
        post.save()
        record(
            "social.post_approved",
            owner_id=owner.pk,
            device_id=device_id,
            target_type="social_post",
            target_id=post.pk,
            detail={"content_sha256": post.content_sha256},
        )
    return post


def create_from_approval(
    owner: User,
    *,
    text: str,
    link: str,
    request_key: str,
    approval: Approval | None,
    now: datetime | None = None,
) -> SocialPost:
    """Called by the assistant tool after the owner approved its exact arguments. A replay of the
    same step returns the same post and never creates or sends a second one."""
    moment = now or timezone.now()
    existing = SocialPost.objects.for_owner(owner).filter(request_key=request_key).first()
    if existing is not None:
        return existing
    account = accounts.active_account(owner)
    if approval is not None and account.connected_at > approval.created_at:
        raise ContentChangedError("The connected Page changed after you approved this.")
    clean, url = content.clean_text(text), content.clean_link(link)
    digest = content.content_hash(account.page_id, clean, url)
    post = SocialPost.objects.create(
        owner=owner,
        account=account,
        page_id=account.page_id,
        page_name=account.page_name,
        text=clean,
        link=url,
        content_sha256=digest,
        source=PostSource.ASSISTANT,
        tainted=bool(approval and approval.tainted),
        request_key=request_key,
        state=PostState.APPROVED,
        approved_at=moment,
        approved_sha256=digest,
        approval_expires_at=moment + timedelta(seconds=settings.SOCIAL_APPROVAL_TTL_SECONDS),
        approved_via="assistant_approval",
        approval=approval,
    )
    record(
        "social.post_created",
        owner_id=owner.pk,
        actor=AuditActor.AGENT,
        target_type="social_post",
        target_id=post.pk,
        detail={"source": "assistant", "approval_id": str(approval.pk) if approval else ""},
    )
    return request_publish(owner, post.pk, now=moment)


# --- publishing -----------------------------------------------------------------------------


def request_publish(owner: User, post_id: uuid.UUID, *, now: datetime | None = None) -> SocialPost:
    """Queue an approved post. Repeating the request is safe and sends nothing extra."""
    moment = now or timezone.now()
    with transaction.atomic():
        post = _locked(owner, post_id)
        if post.state in {PostState.QUEUED, PostState.PUBLISHING, PostState.PUBLISHED}:
            return post
        if post.state == PostState.UNCERTAIN:
            raise UncertainOutcomeError()
        if post.state != PostState.APPROVED:
            raise NotApprovedError()
        if post.approval_expires_at is None or post.approval_expires_at <= moment:
            raise ApprovalExpiredError()
        if post.approved_sha256 != post.content_sha256 or not _intact(post):
            raise ContentChangedError()
        account = accounts.active_account(owner)
        if account.page_id != post.page_id:
            raise SocialNotConnectedError(
                "The connected Page is not the one this was approved for."
            )
        sent_today = (
            SocialPost.objects.for_owner(owner)
            .filter(queued_at__gt=moment - timedelta(hours=24))
            .exclude(state__in=[PostState.CANCELLED])
            .count()
        )
        if sent_today >= settings.SOCIAL_MAX_POSTS_PER_DAY:
            raise DailyLimitError()
        post.state = PostState.QUEUED
        post.queued_at = moment
        post.save()
        _audit(post, "social.publish_requested")
        post_pk = post.pk
        transaction.on_commit(lambda: _enqueue(post_pk))
    return post


def _enqueue(post_id: uuid.UUID) -> None:
    from ..tasks import publish_post

    publish_post.delay(str(post_id))


def _finish(
    post_id: uuid.UUID,
    state: str,
    *,
    code: str = "",
    external_id: str = "",
    published_at: datetime | None = None,
) -> SocialPost | None:
    with transaction.atomic():
        post = SocialPost.objects.select_for_update().filter(pk=post_id).first()
        if post is None:
            return None
        # `uncertain` may only be upgraded by a confirmed answer, never overwritten by a guess.
        allowed = {PostState.PUBLISHING}
        if state in {PostState.PUBLISHED, PostState.FAILED}:
            allowed.add(PostState.UNCERTAIN)
        if post.state not in allowed:
            return post
        post.state = state
        post.error_code = code
        post.lease_until = None
        if external_id:
            post.external_post_id = external_id
        if state == PostState.PUBLISHED:
            post.published_at = published_at or timezone.now()
        post.save()
        _audit(post, f"social.post_{state}", AuditActor.SYSTEM, code=code)
    if state in {PostState.FAILED, PostState.UNCERTAIN} and code not in {"owner_confirmed"}:
        safe_ingest(
            post.owner_id,
            "social",
            f"post:{post.pk}:{state}:{post.publish_attempts}",
            f"social.publish_{state}",
            {"post_id": str(post.pk)},
        )
    return post


def execute_publish(post_id: uuid.UUID, *, now: datetime | None = None) -> str:
    """Send one queued post. Returns the resulting state. Safe to call twice: only `queued`
    is ever sent, and it leaves `queued` before the request is made."""
    moment = now or timezone.now()
    with transaction.atomic():
        post = SocialPost.objects.select_for_update().filter(pk=post_id).first()
        if post is None:
            return "missing"
        if post.state != PostState.QUEUED:
            return str(post.state)
        account = _account_of(post)
        if account is None or not account.is_active or account.page_id != post.page_id:
            return _fail_before_send(post, "account_unavailable")
        if post.approval_expires_at is None or post.approval_expires_at <= moment:
            return _fail_before_send(post, "approval_expired")
        if post.approved_sha256 != post.content_sha256 or not _intact(post):
            return _fail_before_send(post, "approval_mismatch")
        token = accounts.token_of(account)
        post.state = PostState.PUBLISHING
        post.publish_attempts += 1
        post.last_attempt_at = moment
        post.lease_until = moment + timedelta(seconds=settings.SOCIAL_PUBLISH_LEASE_SECONDS)
        post.save()
        args = {"page_id": post.page_id, "token": token, "text": post.text, "link": post.link}
        _audit(post, "social.publish_started", AuditActor.SYSTEM, attempt=post.publish_attempts)
    return _send(post_id, account, args)


def _fail_before_send(post: SocialPost, code: str) -> str:
    post.state = PostState.FAILED
    post.error_code = code
    post.save()
    _audit(post, "social.post_failed", AuditActor.SYSTEM, code=code)
    safe_ingest(
        post.owner_id,
        "social",
        f"post:{post.pk}:failed:{post.publish_attempts}",
        "social.publish_failed",
        {"post_id": str(post.pk)},
    )
    return str(post.state)


def _send(post_id: uuid.UUID, account: SocialAccount, args: dict[str, str]) -> str:
    try:
        external_id = get_provider(account.provider).publish(**args)
    except PublishRejected as exc:
        result = _finish(post_id, PostState.FAILED, code=exc.code)
    except RateLimited as exc:
        result = _finish(post_id, PostState.FAILED, code=exc.code)
    except TokenRejected:
        result = _finish(post_id, PostState.FAILED, code="token_rejected")
        accounts.mark_reauth_required(account)
    except OutcomeUnknown:
        result = _finish(post_id, PostState.UNCERTAIN, code="outcome_unknown")
    except Exception:
        # Anything unexpected after the request may have been sent: assume it might be live.
        logger.exception("social_publish_unexpected")
        result = _finish(post_id, PostState.UNCERTAIN, code="unexpected_error")
    else:
        result = _finish(post_id, PostState.PUBLISHED, external_id=external_id)
    return str(result.state) if result else "missing"


# --- resolving an unknown outcome -----------------------------------------------------------


def reconcile(post_id: uuid.UUID, *, now: datetime | None = None) -> str:
    """Look for the post on the Page. Found: published. Absent for long enough: failed (safe to
    approve again). Otherwise unchanged. Never creates anything."""
    moment = now or timezone.now()
    post = SocialPost.objects.filter(pk=post_id, state=PostState.UNCERTAIN).first()
    if post is None:
        return "not_uncertain"
    account = _account_of(post)
    if account is None or not account.is_active:
        return PostState.UNCERTAIN
    since = (post.last_attempt_at or post.created_at) - RECONCILE_MARGIN
    try:
        remote = get_provider(account.provider).recent_posts(
            page_id=post.page_id, token=accounts.token_of(account), since=since
        )
    except TokenRejected:
        accounts.mark_reauth_required(account)
        return PostState.UNCERTAIN
    except ReadFailed:
        return PostState.UNCERTAIN
    taken = set(
        SocialPost.objects.for_owner(post.owner_id)
        .exclude(external_post_id="")
        .values_list("external_post_id", flat=True)
    )
    wanted = content.comparable(post.text)
    match = next(
        (r for r in remote if r.post_id not in taken and content.comparable(r.message) == wanted),
        None,
    )
    if match is not None:
        _finish(
            post.pk,
            PostState.PUBLISHED,
            code="",
            external_id=match.post_id,
            published_at=match.created_at,
        )
        return PostState.PUBLISHED
    last = post.last_attempt_at or post.created_at
    if moment - last >= timedelta(seconds=settings.SOCIAL_RECONCILE_GRACE_SECONDS):
        _finish(post.pk, PostState.FAILED, code="not_found_on_page")
        return PostState.FAILED
    return PostState.UNCERTAIN


def reconcile_for_owner(owner: User, post_id: uuid.UUID) -> SocialPost:
    post = SocialPost.objects.for_owner(owner).filter(pk=post_id).first()
    if post is None:
        raise NotFound()
    if post.state != PostState.UNCERTAIN:
        raise WrongStateError("Only a post with an unknown outcome can be reconciled.")
    reconcile(post.pk)
    post.refresh_from_db()
    return post


def resolve(owner: User, post_id: uuid.UUID, *, outcome: str) -> SocialPost:
    """The owner looked at their Page and says what happened. Their word is audited."""
    with transaction.atomic():
        post = _locked(owner, post_id)
        if post.state != PostState.UNCERTAIN:
            raise WrongStateError("Only a post with an unknown outcome can be resolved.")
        if outcome == "published":
            post.state, post.published_at = PostState.PUBLISHED, timezone.now()
        else:
            post.state = PostState.FAILED
        post.error_code = "owner_confirmed"
        post.lease_until = None
        post.save()
        _audit(post, "social.post_resolved", outcome=outcome)
    return post


# --- recovery -------------------------------------------------------------------------------


def recover(now: datetime | None = None) -> dict[str, int]:
    """Re-enqueue posts stuck in the queue; give up on sends whose worker disappeared."""
    moment = now or timezone.now()
    requeued = 0
    for post_id in list(
        SocialPost.objects.filter(
            state=PostState.QUEUED, updated_at__lt=moment - timedelta(seconds=STUCK_QUEUE_SECONDS)
        ).values_list("pk", flat=True)[:100]
    ):
        _enqueue(post_id)
        requeued += 1
    lost = 0
    for post_id in list(
        SocialPost.objects.filter(state=PostState.PUBLISHING, lease_until__lt=moment).values_list(
            "pk", flat=True
        )[:100]
    ):
        # The worker may have sent the request before it died: unknown, never "failed".
        if _finish(post_id, PostState.UNCERTAIN, code="worker_lost") is not None:
            lost += 1
    return {"requeued": requeued, "marked_uncertain": lost}


def reconcile_all(now: datetime | None = None) -> int:
    moment = now or timezone.now()
    resolved = 0
    for post_id in list(
        SocialPost.objects.filter(state=PostState.UNCERTAIN).values_list("pk", flat=True)[:100]
    ):
        if reconcile(post_id, now=moment) in {PostState.PUBLISHED, PostState.FAILED}:
            resolved += 1
    return resolved

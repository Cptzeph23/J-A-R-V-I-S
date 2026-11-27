"""Engagement numbers for published posts, and the analytics summary.

Only what the platform reports for our own posts: reactions, comments and shares. Reach and
impressions are NOT collected (their metric names have changed repeatedly and are unverified).
Every figure carries the time it was fetched; a missing figure is absent, never zero.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from apps.accounts.models import User

from ..models import PostMetric, PostState, SocialAccount, SocialPost
from ..providers.base import ReadFailed, TokenRejected
from ..providers.registry import get_provider
from . import accounts

WINDOW_DAYS = 30
BATCH = 50


def _due(owner_id: uuid.UUID | None, now: datetime) -> list[SocialPost]:
    cutoff = now - timedelta(seconds=settings.SOCIAL_METRICS_MIN_INTERVAL_SECONDS)
    rows = (
        SocialPost.objects.filter(
            state=PostState.PUBLISHED,
            published_at__gte=now - timedelta(days=WINDOW_DAYS),
        )
        .exclude(external_post_id="")
        .annotate(latest=Max("metrics__fetched_at"))
        .order_by("published_at")
    )
    if owner_id is not None:
        rows = rows.filter(owner_id=owner_id)
    due: list[SocialPost] = [p for p in rows if p.latest is None or p.latest < cutoff]
    return due[:BATCH]


def refresh(owner: User | None = None, now: datetime | None = None) -> int:
    """Fetch metrics for posts not refreshed recently. Returns how many were stored."""
    moment = now or timezone.now()
    stored = 0
    dead_accounts: set[uuid.UUID] = set()
    for post in _due(owner.pk if owner else None, moment):
        account = (
            SocialAccount.objects.filter(pk=post.account_id).first() if post.account_id else None
        )
        if account is None or not account.is_active or account.pk in dead_accounts:
            continue
        try:
            got = get_provider(account.provider).metrics(
                post_id=post.external_post_id, token=accounts.token_of(account)
            )
        except TokenRejected:
            accounts.mark_reauth_required(account)
            dead_accounts.add(account.pk)
            continue
        except ReadFailed:
            continue
        PostMetric.objects.create(
            owner_id=post.owner_id,
            post=post,
            fetched_at=moment,
            reactions=got.reactions,
            comments=got.comments,
            shares=got.shares,
        )
        stored += 1
    return stored


def summary(owner: User, *, days: int = WINDOW_DAYS, now: datetime | None = None) -> dict[str, Any]:
    moment = now or timezone.now()
    since = moment - timedelta(days=days)
    posts = list(
        SocialPost.objects.for_owner(owner)
        .filter(state=PostState.PUBLISHED, published_at__gte=since)
        .order_by("-published_at")[:100]
    )
    items: list[dict[str, Any]] = []
    totals = {"reactions": 0, "comments": 0, "shares": 0}
    measured = 0
    newest: datetime | None = None
    for post in posts:
        latest = post.metrics.order_by("-fetched_at").first()
        row: dict[str, Any] = {
            "post_id": str(post.pk),
            "published_at": post.published_at,
            "external_post_id": post.external_post_id,
            "reactions": None,
            "comments": None,
            "shares": None,
            "metrics_fetched_at": None,
        }
        if latest is not None:
            row.update(
                reactions=latest.reactions,
                comments=latest.comments,
                shares=latest.shares,
                metrics_fetched_at=latest.fetched_at,
            )
            totals["reactions"] += latest.reactions
            totals["comments"] += latest.comments
            totals["shares"] += latest.shares
            measured += 1
            newest = max(newest, latest.fetched_at) if newest else latest.fetched_at
        items.append(row)
    account = SocialAccount.objects.for_owner(owner).first()
    return {
        "available": account is not None and account.is_active,
        "reason": None if account and account.is_active else "social_not_connected",
        "window_days": days,
        "published_count": len(posts),
        "measured_count": measured,
        "totals": totals if measured else None,
        "posts": items,
        "data_as_of": newest,
        "not_collected": ["reach", "impressions"],
    }

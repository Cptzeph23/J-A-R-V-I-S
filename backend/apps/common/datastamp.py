"""A per-owner "data version" that read-side caches key on.

Any code path that changes data a cached read model depends on bumps the owner's version;
caches that include the version in their key then miss and recompute, so a write is visible
to the very next read instead of after a TTL. The version is bumped immediately (so reads in
the same request or test see it) and again after commit (so a reader that raced the commit and
cached pre-commit data is invalidated too).

Failure is never fatal: if the cache is unreachable the bump is skipped and reads fall back to
computing directly (see `apps.dashboard.cache`).
"""

from __future__ import annotations

import uuid

import structlog
from django.core.cache import cache
from django.db import transaction

logger = structlog.get_logger(__name__)

_TTL_SECONDS = 7 * 24 * 3600


def _key(owner_id: uuid.UUID | str) -> str:
    return f"datastamp:{owner_id}"


def current(owner_id: uuid.UUID | str) -> int:
    """The owner's current version; 0 when none has been recorded or the cache is down."""
    try:
        value = cache.get(_key(owner_id))
    except Exception:
        logger.warning("datastamp_read_failed")
        return 0
    return int(value) if isinstance(value, int) else 0


def _bump(owner_id: uuid.UUID | str) -> None:
    key = _key(owner_id)
    try:
        cache.add(key, 0, _TTL_SECONDS)
        cache.incr(key)
    except Exception:
        logger.warning("datastamp_bump_failed")


def touch(owner_id: uuid.UUID | str) -> None:
    """Record that this owner's data changed."""
    _bump(owner_id)
    transaction.on_commit(lambda: _bump(owner_id))

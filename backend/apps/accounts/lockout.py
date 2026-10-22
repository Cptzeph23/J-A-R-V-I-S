"""Per-account login lockout, kept in the cache (Redis).

After LOGIN_MAX_FAILURES failures inside the window the account is locked for
LOGIN_LOCKOUT_SECONDS. This is keyed by email (not IP) so a distributed attacker cannot
brute-force by rotating addresses. Trade-off, documented in the threat model (T01): an
attacker who knows the owner's email can keep the owner locked out; per-IP throttling and
audit entries are the mitigation. If the cache is unreachable the exception propagates
and login fails closed.
"""

from __future__ import annotations

import hashlib
import time

from django.conf import settings
from django.core.cache import cache


def _digest(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def _counter_key(email: str) -> str:
    return f"login_fail:{_digest(email)}"


def _lock_key(email: str) -> str:
    return f"login_lock:{_digest(email)}"


def locked_seconds_remaining(email: str) -> int:
    """Seconds until the lock lifts, or 0 if the account is not locked."""
    until = cache.get(_lock_key(email))
    if until is None:
        return 0
    return max(0, int(until - time.time()) + 1)


def register_failure(email: str) -> int:
    """Record a failed attempt; lock the account when the threshold is reached."""
    window = settings.LOGIN_LOCKOUT_SECONDS
    key = _counter_key(email)
    cache.add(key, 0, timeout=window)
    try:
        count = int(cache.incr(key))
    except ValueError:  # key expired between add() and incr()
        cache.set(key, 1, timeout=window)
        count = 1
    if count >= settings.LOGIN_MAX_FAILURES:
        cache.set(_lock_key(email), time.time() + window, timeout=window)
    return count


def reset(email: str) -> None:
    cache.delete_many([_counter_key(email), _lock_key(email)])

"""Keyed hash of a sender address; the address itself is not stored (see phonelog.hashing)."""

from __future__ import annotations

import hashlib
import hmac
import unicodedata
import uuid

from django.conf import settings


def normalize_email(raw: str) -> str:
    return unicodedata.normalize("NFKC", raw).strip().casefold()


def sender_hash(owner_id: uuid.UUID, address: str) -> str:
    normalized = normalize_email(address)
    if not normalized or "@" not in normalized:
        return ""
    key = settings.PHONE_HASH_KEY.encode()
    return hmac.new(key, f"mail\x1f{owner_id}\x1f{normalized}".encode(), hashlib.sha256).hexdigest()

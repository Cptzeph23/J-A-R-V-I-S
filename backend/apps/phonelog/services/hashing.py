"""Keyed hashing of the other party's address. The raw number is never stored.

A plain SHA-256 of a phone number is reversible by brute force (the number space is tiny),
so the hash is an HMAC under a server-side secret and is also salted with the owner id: the
same number hashes differently for different owners, and a database dump alone reveals nothing.
Two records with the same counterpart still share a hash for one owner, which is all the
statistics need.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
import uuid

from django.conf import settings

_NON_DIGITS = re.compile(r"[^0-9]")
MIN_NUMBER_DIGITS = 5


def normalize_address(raw: str) -> str:
    """Canonical form of a phone number or alphanumeric sender id; "" when unusable.

    +254712345678, 0712 345 678, 254712345678 and 00254712345678 all normalize to the same
    digits. Alphanumeric ids (a bank's sender name) are compared case-insensitively.
    """
    text = unicodedata.normalize("NFKC", raw).strip()
    if not text:
        return ""
    if any(ch.isalpha() for ch in text):
        return "id:" + " ".join(text.casefold().split())
    digits = _NON_DIGITS.sub("", text)
    if text.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = settings.PHONE_DEFAULT_COUNTRY_CODE + digits[1:]
    if len(digits) < MIN_NUMBER_DIGITS:
        return ""
    return "tel:" + digits


def counterpart_hash(owner_id: uuid.UUID, raw: str) -> str:
    normalized = normalize_address(raw)
    if not normalized:
        return ""
    key = settings.PHONE_HASH_KEY.encode("utf-8")
    message = f"{owner_id}\x1f{normalized}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()

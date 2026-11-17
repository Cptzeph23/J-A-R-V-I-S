"""Authenticated encryption for secrets at rest (OAuth tokens, mail subjects).

Format: `v1.<key_id>.<base64url(nonce || ciphertext+tag)>`, AES-256-GCM.

- Keys come from `FIELD_ENCRYPTION_KEYS`: `id:base64key` pairs separated by commas, 32 bytes
  each. The first pair encrypts; every pair can decrypt, so a key is rotated by adding a new
  first pair and re-sealing old rows (`reseal`), then removing the old pair.
- `context` is authenticated but not stored (for example `oauth.refresh:<credential id>`). A
  value copied into another row or field fails to open, so a database writer cannot swap one
  owner's secret into another's record.
- Production refuses to start without explicit keys. Development and tests derive one key from
  the Django secret so nothing needs configuring.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

VERSION = "v1"
KEY_BYTES = 32
NONCE_BYTES = 12


class SecretBoxError(Exception):
    """Raised without the value or the key in the message."""


@lru_cache(maxsize=8)
def _parse(spec: str, fallback_seed: str) -> tuple[tuple[str, bytes], ...]:
    keys: list[tuple[str, bytes]] = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        key_id, sep, encoded = part.partition(":")
        if not sep or not key_id.replace("-", "").replace("_", "").isalnum():
            raise SecretBoxError(
                "FIELD_ENCRYPTION_KEYS must look like id:base64key[,id:base64key]."
            )
        try:
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except (binascii.Error, ValueError) as exc:
            raise SecretBoxError("An encryption key is not valid base64.") from exc
        if len(raw) != KEY_BYTES:
            raise SecretBoxError(f"Each encryption key must be {KEY_BYTES} bytes.")
        keys.append((key_id, raw))
    if not keys:
        derived = hashlib.sha256(b"jarvis-secretbox-dev\x00" + fallback_seed.encode()).digest()
        keys.append(("dev", derived))
    if len({k for k, _ in keys}) != len(keys):
        raise SecretBoxError("Encryption key ids must be unique.")
    return tuple(keys)


def _keyring() -> tuple[tuple[str, bytes], ...]:
    return _parse(getattr(settings, "FIELD_ENCRYPTION_KEYS", ""), settings.SECRET_KEY)


def generate_key() -> str:
    """A new key in the form accepted by FIELD_ENCRYPTION_KEYS (without the id)."""
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).decode().rstrip("=")


def active_key_id() -> str:
    return _keyring()[0][0]


def seal(plaintext: str, context: str) -> str:
    key_id, key = _keyring()[0]
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), context.encode("utf-8"))
    blob = base64.urlsafe_b64encode(nonce + ciphertext).decode().rstrip("=")
    return f"{VERSION}.{key_id}.{blob}"


def key_id_of(token: str) -> str:
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        raise SecretBoxError("Unrecognized sealed value.")
    return parts[1]


def open_sealed(token: str, context: str) -> str:
    key_id = key_id_of(token)
    key = dict(_keyring()).get(key_id)
    if key is None:
        raise SecretBoxError("The key that sealed this value is no longer configured.")
    blob = token.split(".")[2]
    try:
        raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
        return AESGCM(key).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], context.encode()).decode()
    except (InvalidTag, binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise SecretBoxError("The value could not be opened (wrong context or tampered).") from exc


def reseal(token: str, context: str) -> str:
    """Re-encrypt under the active key; unchanged if it already uses it."""
    if key_id_of(token) == active_key_id():
        return token
    return seal(open_sealed(token, context), context)

"""Login, refresh-token rotation and logout.

Rules enforced here (docs/04-SRS.md SRS-SEC-01):
- Wrong email and wrong password are indistinguishable (same error, equalised timing).
- A refresh token is single-use. Presenting a used token again, outside a short leeway,
  is treated as theft: the whole device session is revoked.
- Failures are raised only AFTER the transaction that recorded them has committed, so the
  revocation and audit entry of a rejected request are never rolled back.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, Throttled

from apps.audit.recorder import record
from apps.devices.models import Device, RefreshToken
from apps.devices.services.sessions import revoke_session

from .. import lockout, tokens
from ..models import User, normalize_email


@dataclass(frozen=True, slots=True)
class TokenPair:
    device: Device
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


def _email_digest(email: str) -> str:
    """Truncated digest for the audit log. The log is append-only, so a raw attempted
    email (which may be a mistyped password) must never be written to it."""
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]


@cache
def _dummy_hash() -> str:
    """A valid hash to verify against when the email is unknown (timing equalisation)."""
    return make_password("not-a-real-password-used-only-for-timing")


def _issue_refresh(
    user: User, device: Device, *, family_id: uuid.UUID, now: datetime
) -> tuple[str, datetime]:
    secret = tokens.new_refresh_secret()
    expires = now + timedelta(seconds=settings.REFRESH_TOKEN_LIFETIME_SECONDS)
    RefreshToken.objects.create(
        owner=user,
        device=device,
        family_id=family_id,
        token_hash=tokens.hash_refresh_secret(secret),
        expires_at=expires,
    )
    return secret, expires


def _get_or_create_device(
    user: User,
    *,
    platform: str,
    name: str,
    app_version: str,
    device_id: uuid.UUID | None,
    now: datetime,
) -> Device:
    if device_id is not None:
        existing = Device.objects.filter(id=device_id, owner=user, revoked_at__isnull=True).first()
        if existing is not None:
            existing.name = name
            existing.app_version = app_version
            existing.last_seen_at = now
            existing.save(update_fields=["name", "app_version", "last_seen_at", "updated_at"])
            return existing
    return Device.objects.create(
        owner=user, platform=platform, name=name, app_version=app_version, last_seen_at=now
    )


def login(
    *,
    email: str,
    password: str,
    platform: str,
    device_name: str,
    app_version: str = "",
    device_id: uuid.UUID | None = None,
    ip: str | None = None,
) -> TokenPair:
    email = normalize_email(email)

    remaining = lockout.locked_seconds_remaining(email)
    if remaining:
        record(
            "auth.login_locked",
            actor="system",
            ip=ip,
            detail={"attempted_email_sha256": _email_digest(email)},
        )
        raise Throttled(wait=float(remaining))

    user = User.objects.filter(email=email, is_active=True).first()
    if user is None:
        check_password(password, _dummy_hash())
        valid = False
    else:
        valid = user.check_password(password)

    if user is None or not valid:
        failures = lockout.register_failure(email)
        record(
            "auth.login_failed",
            owner_id=user.id if user else None,
            ip=ip,
            detail={
                "attempted_email_sha256": _email_digest(email),
                "consecutive_failures": failures,
            },
        )
        raise AuthenticationFailed("Invalid credentials.")

    lockout.reset(email)
    now = timezone.now()
    with transaction.atomic():
        device = _get_or_create_device(
            user,
            platform=platform,
            name=device_name,
            app_version=app_version,
            device_id=device_id,
            now=now,
        )
        refresh_secret, refresh_expires = _issue_refresh(
            user, device, family_id=uuid.uuid4(), now=now
        )
        user.last_login = now
        user.save(update_fields=["last_login"])
        record(
            "auth.login_succeeded",
            owner_id=user.id,
            device_id=device.id,
            ip=ip,
            target_type="device",
            target_id=device.id,
            detail={"platform": device.platform},
        )
    access, access_expires = tokens.issue_access_token(user_id=user.id, device_id=device.id)
    return TokenPair(device, access, access_expires, refresh_secret, refresh_expires)


def refresh(*, refresh_token: str, ip: str | None = None) -> TokenPair:
    now = timezone.now()
    token_hash = tokens.hash_refresh_secret(refresh_token)
    pair: TokenPair | None = None

    with transaction.atomic():
        row = (
            RefreshToken.objects.select_for_update(of=("self",))
            .select_related("device", "owner")
            .filter(token_hash=token_hash)
            .first()
        )
        if row is None or row.revoked_at is not None:
            pass  # unknown or revoked: indistinguishable to the caller
        elif row.used_at is not None:
            elapsed = (now - row.used_at).total_seconds()
            if elapsed <= settings.REFRESH_REUSE_LEEWAY_SECONDS:
                record(
                    "auth.refresh_replayed",
                    owner_id=row.owner_id,
                    device_id=row.device_id,
                    ip=ip,
                    detail={"seconds_since_use": round(elapsed, 1)},
                )
            else:
                revoke_session(row.device, now=now)
                record(
                    "auth.refresh_reuse_detected",
                    owner_id=row.owner_id,
                    actor="system",
                    device_id=row.device_id,
                    ip=ip,
                    target_type="device",
                    target_id=row.device_id,
                    detail={"family_id": str(row.family_id), "action": "device_revoked"},
                )
        elif row.is_expired(now) or not row.device.is_active or not row.owner.is_active:
            pass
        else:
            row.used_at = now
            row.save(update_fields=["used_at", "updated_at"])
            secret, expires = _issue_refresh(
                row.owner, row.device, family_id=row.family_id, now=now
            )
            row.device.last_seen_at = now
            row.device.save(update_fields=["last_seen_at", "updated_at"])
            access, access_expires = tokens.issue_access_token(
                user_id=row.owner_id, device_id=row.device_id
            )
            pair = TokenPair(row.device, access, access_expires, secret, expires)

    if pair is None:
        raise AuthenticationFailed("Invalid refresh token.")
    return pair


def logout(*, device: Device, ip: str | None = None) -> None:
    """End this device's session (revokes the device and all its refresh tokens)."""
    with transaction.atomic():
        revoke_session(device)
        record(
            "auth.logout",
            owner_id=device.owner_id,
            device_id=device.id,
            ip=ip,
            target_type="device",
            target_id=device.id,
        )

"""DRF authentication: Bearer access token, checked against the live device row."""

from __future__ import annotations

from dataclasses import dataclass

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from apps.devices.models import Device

from .models import User
from .tokens import TokenError, decode_access_token


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Stored in request.auth: which device session made this request."""

    device: Device
    jti: str


class JWTAuthentication(BaseAuthentication):
    """Authenticate `Authorization: Bearer <jwt>`.

    The signature alone is not enough: the device must still exist, belong to the token's
    user, and not be revoked, so revoking a device takes effect on the very next request.
    Returns None (not authenticated) when no Bearer header is present.
    """

    def authenticate_header(self, request: Request) -> str:
        return 'Bearer realm="api"'

    def authenticate(self, request: Request) -> tuple[User, AuthContext] | None:
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != b"bearer":
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("Malformed Authorization header.")
        try:
            claims = decode_access_token(parts[1].decode("ascii"))
        except (TokenError, UnicodeDecodeError) as exc:
            raise AuthenticationFailed("Invalid token.") from exc

        device = (
            Device.objects.select_related("owner")
            .filter(id=claims.device_id, owner_id=claims.user_id, revoked_at__isnull=True)
            .first()
        )
        if device is None or not device.owner.is_active:
            raise AuthenticationFailed("Session is no longer valid.")
        return device.owner, AuthContext(device=device, jti=claims.jti)

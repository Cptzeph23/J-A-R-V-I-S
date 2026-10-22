import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import tokens
from tests.factories import DeviceFactory

pytestmark = pytest.mark.django_db

PROTECTED = "/api/v1/devices"


def call(header: str | None):
    client = APIClient()
    if header is not None:
        client.credentials(HTTP_AUTHORIZATION=header)
    return client.get(PROTECTED)


def assert_401(response):
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert response["WWW-Authenticate"].startswith("Bearer")


def token_for(user, device, **kwargs):
    return tokens.issue_access_token(user_id=user.id, device_id=device.id, **kwargs)[0]


def test_valid_token_is_accepted(client_a):
    assert client_a.get(PROTECTED).status_code == 200


def test_missing_header_is_401():
    assert_401(call(None))


@pytest.mark.parametrize(
    "header",
    ["Basic dXNlcjpwYXNz", "Token abc", "Bearer", "Bearer a b", "Bearer not.a.jwt", "bearer-only"],
)
def test_malformed_credentials_are_401(header):
    assert_401(call(header))


def test_lowercase_scheme_is_accepted(owner_a):
    device = DeviceFactory(owner=owner_a)
    assert call(f"bearer {token_for(owner_a, device)}").status_code == 200


def test_expired_token_is_401(owner_a):
    device = DeviceFactory(owner=owner_a)
    old = datetime.now(UTC) - timedelta(hours=1)
    assert_401(call(f"Bearer {token_for(owner_a, device, now=old)}"))


def test_token_signed_with_a_wrong_key_is_401(owner_a):
    device = DeviceFactory(owner=owner_a)
    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "sub": str(owner_a.id),
            "did": str(device.id),
            "jti": "x",
            "typ": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        "attacker-controlled-key-attacker-controlled-key",
        algorithm="HS256",
    )
    assert_401(call(f"Bearer {forged}"))


def test_revoked_device_is_rejected_immediately(owner_a):
    device = DeviceFactory(owner=owner_a)
    header = f"Bearer {token_for(owner_a, device)}"
    assert call(header).status_code == 200
    device.revoked_at = timezone.now()
    device.save()
    assert_401(call(header))


def test_deleted_device_is_rejected(owner_a):
    device = DeviceFactory(owner=owner_a)
    header = f"Bearer {token_for(owner_a, device)}"
    device.delete()
    assert_401(call(header))


def test_inactive_user_is_rejected(owner_a):
    device = DeviceFactory(owner=owner_a)
    header = f"Bearer {token_for(owner_a, device)}"
    owner_a.is_active = False
    owner_a.save()
    assert_401(call(header))


def test_token_cannot_be_paired_with_another_owners_device(owner_a, owner_b):
    """A valid signature naming A's id but B's device must not authenticate."""
    foreign_device = DeviceFactory(owner=owner_b)
    assert_401(call(f"Bearer {token_for(owner_a, foreign_device)}"))


def test_token_for_unknown_user_and_device_is_401(db):
    token, _ = tokens.issue_access_token(user_id=uuid.uuid4(), device_id=uuid.uuid4())
    assert_401(call(f"Bearer {token}"))


def test_request_user_and_auth_context_are_populated(client_a, owner_a):
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    from apps.accounts.authentication import JWTAuthentication

    request = APIRequestFactory().get(
        PROTECTED, HTTP_AUTHORIZATION=f"Bearer {client_a.pair.access_token}"
    )
    result = JWTAuthentication().authenticate(Request(request))
    assert result is not None
    user, context = result
    assert user == owner_a
    assert context.device.id == client_a.pair.device.id

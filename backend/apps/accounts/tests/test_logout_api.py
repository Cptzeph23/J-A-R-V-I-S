import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditEntry
from apps.devices.models import Device, RefreshToken
from tests.auth_fixtures import authenticate
from tests.idor import assert_unauthenticated_rejected

pytestmark = pytest.mark.django_db

LOGOUT = "/api/v1/auth/logout"
DEVICES = "/api/v1/devices"


def test_logout_ends_the_session(client_a):
    assert client_a.get(DEVICES).status_code == 200
    assert client_a.post(LOGOUT).status_code == 204
    assert client_a.get(DEVICES).status_code == 401


def test_logout_revokes_device_and_refresh_tokens(client_a):
    client_a.post(LOGOUT)
    assert Device.objects.get(id=client_a.device.id).revoked_at is not None
    assert not RefreshToken.objects.filter(revoked_at__isnull=True).exists()
    response = APIClient().post(
        "/api/v1/auth/refresh", {"refresh_token": client_a.pair.refresh_token}
    )
    assert response.status_code == 401


def test_logout_is_audited(client_a, owner_a):
    client_a.post(LOGOUT)
    entry = AuditEntry.objects.get(action="auth.logout")
    assert entry.owner_id == owner_a.id
    assert entry.device_id == client_a.device.id


def test_logout_only_affects_the_calling_device(owner_a):
    phone = authenticate(owner_a, name="Phone")
    laptop = authenticate(owner_a, name="Laptop", platform="web")
    phone.post(LOGOUT)
    assert laptop.get(DEVICES).status_code == 200


def test_logout_requires_authentication():
    assert_unauthenticated_rejected("post", LOGOUT)


def test_second_logout_with_the_same_token_is_401(client_a):
    assert client_a.post(LOGOUT).status_code == 204
    assert client_a.post(LOGOUT).status_code == 401

from __future__ import annotations

import pytest

from apps.audit.models import AuditEntry
from apps.devices.models import Device
from tests.auth_fixtures import AuthedClient

pytestmark = pytest.mark.django_db

TOKEN = "fcm-token-" + "a" * 40


def url(device: Device) -> str:
    return f"/api/v1/devices/{device.pk}/push-token"


def test_register_stores_the_token_sealed_and_never_returns_it(client_a: AuthedClient):
    response = client_a.post(
        url(client_a.device), {"provider": "fcm", "token": TOKEN}, format="json"
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["push_enabled"], body["push_provider"]) == (True, "fcm")
    assert TOKEN not in response.content.decode()
    row = Device.objects.get(pk=client_a.device.pk)
    assert TOKEN not in row.push_token_sealed
    assert row.push_token_sealed.startswith("v1.")


def test_the_token_never_appears_in_the_audit_log(client_a: AuthedClient):
    client_a.post(url(client_a.device), {"provider": "fcm", "token": TOKEN}, format="json")
    entry = AuditEntry.objects.get(action="device.push_token_registered")
    assert TOKEN not in str(entry.detail)
    assert entry.detail == {"provider": "fcm"}


def test_registering_again_is_harmless_and_updates(client_a: AuthedClient):
    client_a.post(url(client_a.device), {"provider": "fcm", "token": TOKEN}, format="json")
    again = client_a.post(url(client_a.device), {"provider": "fcm", "token": TOKEN}, format="json")
    assert again.status_code == 200
    assert Device.objects.filter(push_token_hash__gt="").count() == 1


def test_a_token_moving_to_another_device_leaves_the_old_one(client_a: AuthedClient, make_client):
    other = make_client(email="second@example.com")
    client_a.post(url(client_a.device), {"provider": "fcm", "token": TOKEN}, format="json")
    other.post(url(other.device), {"provider": "fcm", "token": TOKEN}, format="json")
    assert Device.objects.get(pk=client_a.device.pk).push_token_hash == ""
    assert Device.objects.get(pk=other.device.pk).push_token_hash != ""


@pytest.mark.parametrize(
    "body",
    [
        {"provider": "sms", "token": TOKEN},
        {"provider": "fcm", "token": "short"},
        {"provider": "fcm"},
        {"token": TOKEN},
        {"provider": "fcm", "token": "x" * 5000},
    ],
)
def test_invalid_registrations_are_rejected(client_a: AuthedClient, body):
    assert client_a.post(url(client_a.device), body, format="json").status_code == 400


def test_remove_clears_the_registration_and_is_idempotent(client_a: AuthedClient):
    client_a.post(url(client_a.device), {"provider": "fcm", "token": TOKEN}, format="json")
    assert client_a.delete(url(client_a.device)).status_code == 204
    assert client_a.delete(url(client_a.device)).status_code == 204
    row = Device.objects.get(pk=client_a.device.pk)
    assert (row.push_token_hash, row.push_token_sealed, row.push_provider) == ("", "", "")


def test_another_owners_device_is_404(client_a: AuthedClient, client_b: AuthedClient):
    body = {"provider": "fcm", "token": TOKEN}
    assert client_a.post(url(client_b.device), body, format="json").status_code == 404
    assert client_a.delete(url(client_b.device)).status_code == 404
    assert Device.objects.get(pk=client_b.device.pk).push_token_hash == ""


def test_a_revoked_device_cannot_register(client_a: AuthedClient, make_client):
    other = make_client(email="third@example.com")
    Device.objects.filter(pk=other.device.pk).update(revoked_at="2026-11-18T00:00:00Z")
    assert (
        client_a.post(
            url(other.device), {"provider": "fcm", "token": TOKEN}, format="json"
        ).status_code
        == 404
    )


def test_unauthenticated_is_refused(client, client_a: AuthedClient):
    assert (
        client.post(
            url(client_a.device), {"provider": "fcm", "token": TOKEN}, format="json"
        ).status_code
        == 401
    )


def test_the_device_list_shows_push_state_without_the_token(client_a: AuthedClient):
    client_a.post(url(client_a.device), {"provider": "fcm", "token": TOKEN}, format="json")
    listing = client_a.get("/api/v1/devices").json()
    row = listing["results"][0] if "results" in listing else listing[0]
    assert row["push_enabled"] is True
    assert TOKEN not in str(listing)

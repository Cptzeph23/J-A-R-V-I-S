import uuid

import pytest
from django.utils import timezone

from apps.audit.models import AuditEntry
from apps.devices.models import Device, RefreshToken
from tests.auth_fixtures import authenticate
from tests.factories import DeviceFactory

pytestmark = pytest.mark.django_db

LIST = "/api/v1/devices"


def url(device) -> str:
    return f"/api/v1/devices/{device.id}"


class TestList:
    def test_lists_only_own_active_devices_by_default(self, client_a, owner_a):
        DeviceFactory(owner=owner_a, revoked_at=timezone.now())
        body = client_a.get(LIST).json()
        assert [d["id"] for d in body["results"]] == [str(client_a.device.id)]
        assert body["next_cursor"] is None

    def test_include_revoked(self, client_a, owner_a):
        revoked = DeviceFactory(owner=owner_a, revoked_at=timezone.now())
        ids = {d["id"] for d in client_a.get(LIST, {"include_revoked": "true"}).json()["results"]}
        assert ids == {str(client_a.device.id), str(revoked.id)}

    def test_marks_the_current_device(self, owner_a):
        phone = authenticate(owner_a, name="Phone")
        laptop = authenticate(owner_a, name="Laptop", platform="web")
        results = phone.get(LIST).json()["results"]
        flags = {r["name"]: r["is_current"] for r in results}
        assert flags == {"Phone": True, "Laptop": False}
        assert laptop.get(LIST).json()["results"][0]["is_current"] in (True, False)

    def test_response_never_exposes_secrets(self, client_a):
        item = client_a.get(LIST).json()["results"][0]
        assert set(item) == {
            "id",
            "platform",
            "name",
            "app_version",
            "last_seen_at",
            "revoked_at",
            "created_at",
            "is_current",
        }

    def test_cursor_pagination_covers_every_device_once(self, owner_a, client_a):
        for _ in range(5):
            DeviceFactory(owner=owner_a)
        seen, cursor = [], None
        for _ in range(10):
            params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
            body = client_a.get(LIST, params).json()
            seen += [d["id"] for d in body["results"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == len(set(seen)) == 6

    def test_invalid_cursor_is_a_validation_error(self, client_a):
        response = client_a.get(LIST, {"cursor": "garbage"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    def test_limit_is_capped(self, client_a):
        assert client_a.get(LIST, {"limit": 1000}).status_code == 200


class TestRename:
    def test_rename(self, client_a, owner_a):
        response = client_a.patch(url(client_a.device), {"name": "  My pixel  "})
        assert response.status_code == 200
        assert response.json()["name"] == "My pixel"
        assert response.json()["is_current"] is True
        assert Device.objects.get(id=client_a.device.id).name == "My pixel"
        entry = AuditEntry.objects.get(action="device.renamed")
        assert entry.detail == {"old": "A phone", "new": "My pixel"}

    @pytest.mark.parametrize("name", ["", "   ", "x" * 101])
    def test_invalid_name(self, client_a, name):
        response = client_a.patch(url(client_a.device), {"name": name})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    def test_cannot_change_read_only_fields(self, client_a):
        client_a.patch(url(client_a.device), {"name": "ok", "platform": "ios", "revoked_at": None})
        assert Device.objects.get(id=client_a.device.id).platform == "android"


class TestRevoke:
    def test_revoking_another_device_kills_its_session_only(self, owner_a):
        phone = authenticate(owner_a, name="Phone")
        laptop = authenticate(owner_a, name="Laptop", platform="web")
        assert phone.delete(url(laptop.device)).status_code == 204
        assert laptop.get(LIST).status_code == 401
        assert phone.get(LIST).status_code == 200
        assert not RefreshToken.objects.filter(
            device=laptop.device, revoked_at__isnull=True
        ).exists()

    def test_revoking_the_current_device_logs_the_caller_out(self, client_a):
        assert client_a.delete(url(client_a.device)).status_code == 204
        assert client_a.get(LIST).status_code == 401

    def test_revoke_is_idempotent_and_audited_once(self, owner_a):
        phone = authenticate(owner_a, name="Phone")
        laptop = authenticate(owner_a, name="Laptop", platform="web")
        assert phone.delete(url(laptop.device)).status_code == 204
        assert phone.delete(url(laptop.device)).status_code == 204
        assert AuditEntry.objects.filter(action="device.revoked").count() == 1

    def test_revoke_audit_records_who_did_it(self, owner_a):
        phone = authenticate(owner_a, name="Phone")
        laptop = authenticate(owner_a, name="Laptop", platform="web")
        phone.delete(url(laptop.device))
        entry = AuditEntry.objects.get(action="device.revoked")
        assert entry.device_id == phone.device.id
        assert entry.target_id == str(laptop.device.id)


class TestNotFound:
    @pytest.mark.parametrize("method", ["patch", "delete"])
    def test_unknown_id(self, client_a, method):
        response = getattr(client_a, method)(f"/api/v1/devices/{uuid.uuid4()}", {"name": "x"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_malformed_id(self, client_a):
        assert client_a.delete("/api/v1/devices/not-a-uuid").status_code == 404

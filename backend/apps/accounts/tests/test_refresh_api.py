import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient

from apps.accounts.services import auth
from apps.audit.models import AuditEntry
from apps.devices.models import Device, RefreshToken
from tests.factories import DEFAULT_PASSWORD, UserFactory

REFRESH = "/api/v1/auth/refresh"
DEVICES = "/api/v1/devices"


@pytest.fixture
def session(db):
    user = UserFactory()
    pair = auth.login(
        email=user.email, password=DEFAULT_PASSWORD, platform="android", device_name="Pixel"
    )
    return user, pair


def bearer(token: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def do_refresh(token: str):
    return APIClient().post(REFRESH, {"refresh_token": token})


@pytest.mark.django_db
class TestRotation:
    def test_refresh_returns_a_new_working_pair(self, session):
        _, pair = session
        response = do_refresh(pair.refresh_token)
        assert response.status_code == 200
        body = response.json()
        assert body["refresh_token"] != pair.refresh_token
        assert body["access_token"] != pair.access_token
        assert body["device"]["id"] == str(pair.device.id)
        assert bearer(body["access_token"]).get(DEVICES).status_code == 200

    def test_old_token_is_marked_used_and_new_one_shares_the_family(self, session):
        _, pair = session
        do_refresh(pair.refresh_token)
        rows = list(RefreshToken.objects.order_by("created_at"))
        assert len(rows) == 2
        assert rows[0].used_at is not None
        assert rows[1].used_at is None
        assert rows[0].family_id == rows[1].family_id

    def test_refresh_chain_keeps_working(self, session):
        _, pair = session
        token = pair.refresh_token
        for _ in range(3):
            response = do_refresh(token)
            assert response.status_code == 200
            token = response.json()["refresh_token"]
            RefreshToken.objects.filter(used_at__isnull=False).update(
                used_at=timezone.now() - timedelta(minutes=5)
            )

    def test_refresh_updates_last_seen(self, session):
        _, pair = session
        Device.objects.update(last_seen_at=timezone.now() - timedelta(days=1))
        do_refresh(pair.refresh_token)
        last_seen = Device.objects.get().last_seen_at
        assert last_seen is not None
        assert last_seen > timezone.now() - timedelta(minutes=1)


@pytest.mark.django_db
class TestRejection:
    def assert_rejected(self, response):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    def test_unknown_token(self, db):
        self.assert_rejected(do_refresh("a-token-that-never-existed"))

    def test_expired_token(self, session):
        _, pair = session
        RefreshToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assert_rejected(do_refresh(pair.refresh_token))

    def test_revoked_token(self, session):
        _, pair = session
        RefreshToken.objects.update(revoked_at=timezone.now())
        self.assert_rejected(do_refresh(pair.refresh_token))

    def test_revoked_device(self, session):
        _, pair = session
        Device.objects.update(revoked_at=timezone.now())
        self.assert_rejected(do_refresh(pair.refresh_token))

    def test_inactive_user(self, session):
        user, pair = session
        user.is_active = False
        user.save()
        self.assert_rejected(do_refresh(pair.refresh_token))

    def test_all_failures_look_identical(self, session):
        _, pair = session
        unknown = do_refresh("nope").json()
        RefreshToken.objects.update(revoked_at=timezone.now())
        revoked = do_refresh(pair.refresh_token).json()
        for body in (unknown, revoked):
            body["error"].pop("request_id")
        assert unknown == revoked

    @pytest.mark.parametrize("body", [{}, {"refresh_token": ""}, {"refresh_token": "x" * 257}])
    def test_validation(self, db, body):
        response = APIClient().post(REFRESH, body)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    def test_access_token_is_not_accepted_as_refresh_token(self, session):
        _, pair = session
        # Too long to be a refresh secret, so rejected at validation; never a 200.
        response = do_refresh(pair.access_token)
        assert response.status_code in (400, 401)
        assert "access_token" not in response.json()
        assert RefreshToken.objects.filter(used_at__isnull=False).count() == 0

    def test_rate_limit(self, db):
        codes = [do_refresh(f"token-{i}").status_code for i in range(32)]
        assert codes[:30] == [401] * 30
        assert codes[30:] == [429, 429]


@pytest.mark.django_db
class TestTheftDetection:
    def backdate_use(self):
        RefreshToken.objects.filter(used_at__isnull=False).update(
            used_at=timezone.now() - timedelta(minutes=5)
        )

    def test_reusing_an_old_token_revokes_the_whole_session(self, session):
        _, pair = session
        rotated = do_refresh(pair.refresh_token).json()
        self.backdate_use()

        replay = do_refresh(pair.refresh_token)
        assert replay.status_code == 401

        assert Device.objects.get().revoked_at is not None
        assert not RefreshToken.objects.filter(revoked_at__isnull=True).exists()
        # The thief's stolen token and the victim's newer tokens are all dead.
        assert do_refresh(rotated["refresh_token"]).status_code == 401
        assert bearer(rotated["access_token"]).get(DEVICES).status_code == 401

    def test_theft_is_audited(self, session):
        user, pair = session
        do_refresh(pair.refresh_token)
        self.backdate_use()
        do_refresh(pair.refresh_token)
        entry = AuditEntry.objects.get(action="auth.refresh_reuse_detected")
        assert entry.owner_id == user.id
        assert entry.detail["action"] == "device_revoked"

    def test_replay_inside_leeway_is_rejected_without_revoking(self, session):
        _, pair = session
        rotated = do_refresh(pair.refresh_token).json()

        assert do_refresh(pair.refresh_token).status_code == 401  # immediate duplicate

        assert Device.objects.get().revoked_at is None
        assert AuditEntry.objects.filter(action="auth.refresh_replayed").exists()
        assert not AuditEntry.objects.filter(action="auth.refresh_reuse_detected").exists()
        assert do_refresh(rotated["refresh_token"]).status_code == 200

    def test_other_devices_survive_theft_on_one_device(self, session):
        user, pair = session
        other = auth.login(
            email=user.email, password=DEFAULT_PASSWORD, platform="web", device_name="Laptop"
        )
        do_refresh(pair.refresh_token)
        self.backdate_use()
        do_refresh(pair.refresh_token)
        assert bearer(other.access_token).get(DEVICES).status_code == 200
        assert do_refresh(other.refresh_token).status_code == 200


@pytest.mark.django_db(transaction=True)
def test_concurrent_refresh_of_one_token_succeeds_exactly_once():
    user = UserFactory()
    pair = auth.login(
        email=user.email, password=DEFAULT_PASSWORD, platform="android", device_name="Pixel"
    )
    workers = 4
    barrier = threading.Barrier(workers)

    def attempt() -> bool:
        try:
            barrier.wait(timeout=10)
            auth.refresh(refresh_token=pair.refresh_token)
            return True
        except AuthenticationFailed:
            return False
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda _: attempt(), range(workers)))

    assert results.count(True) == 1
    assert Device.objects.get().revoked_at is None  # losers were replays, not theft
    assert RefreshToken.objects.count() == 2

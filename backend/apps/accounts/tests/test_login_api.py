from unittest import mock

import pytest

from apps.audit.models import AuditEntry
from apps.devices.models import Device, RefreshToken
from tests.factories import DEFAULT_PASSWORD, DeviceFactory, UserFactory

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login"


def payload(email="owner@example.com", password=DEFAULT_PASSWORD, **device):
    info = {"platform": "android", "name": "Pixel", "app_version": "1.0.0"}
    info.update(device)
    return {"email": email, "password": password, "device": info}


@pytest.fixture
def owner():
    return UserFactory(email="owner@example.com")


def audit_actions():
    return list(AuditEntry.objects.order_by("occurred_at").values_list("action", flat=True))


def test_successful_login_returns_tokens_and_device(api_client, owner):
    response = api_client.post(LOGIN, payload())
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["access_expires_at"] < body["refresh_expires_at"]
    assert body["device"]["platform"] == "android"
    assert body["device"]["name"] == "Pixel"
    assert body["device"]["is_current"] is True
    assert Device.objects.filter(owner=owner).count() == 1


def test_refresh_token_is_stored_hashed_only(api_client, owner):
    body = api_client.post(LOGIN, payload()).json()
    row = RefreshToken.objects.get()
    assert row.token_hash != body["refresh_token"]
    assert body["refresh_token"] not in row.token_hash
    assert len(row.token_hash) == 64


def test_login_updates_last_login_and_audits(api_client, owner):
    api_client.post(LOGIN, payload())
    owner.refresh_from_db()
    assert owner.last_login is not None
    entry = AuditEntry.objects.get(action="auth.login_succeeded")
    assert entry.owner_id == owner.id
    assert entry.ip == "127.0.0.1"


def test_audit_never_contains_password_or_tokens(api_client, owner):
    body = api_client.post(LOGIN, payload()).json()
    api_client.post(LOGIN, payload(password="wrong-password-value"))
    dump = " ".join(str(e.detail) for e in AuditEntry.objects.all())
    assert DEFAULT_PASSWORD not in dump
    assert "wrong-password-value" not in dump
    assert body["refresh_token"] not in dump
    assert body["access_token"] not in dump


def test_email_is_case_insensitive(api_client, owner):
    assert api_client.post(LOGIN, payload(email="  OWNER@Example.com ")).status_code == 200


def test_wrong_password_and_unknown_email_are_indistinguishable(api_client, owner):
    wrong = api_client.post(LOGIN, payload(password="not-the-password"))
    unknown = api_client.post(LOGIN, payload(email="nobody@example.com"))
    assert wrong.status_code == unknown.status_code == 401
    wrong_body, unknown_body = wrong.json(), unknown.json()
    for body in (wrong_body, unknown_body):
        body["error"].pop("request_id")
    assert wrong_body == unknown_body
    assert wrong_body["error"]["code"] == "unauthenticated"


def test_unknown_email_still_pays_for_a_password_hash(api_client, owner):
    with mock.patch("apps.accounts.services.auth.check_password") as dummy_check:
        api_client.post(LOGIN, payload(email="nobody@example.com"))
    dummy_check.assert_called_once()


def test_inactive_user_cannot_log_in(api_client):
    UserFactory(email="off@example.com", is_active=False)
    assert api_client.post(LOGIN, payload(email="off@example.com")).status_code == 401


def test_failed_login_is_audited_with_owner_when_known(api_client, owner):
    api_client.post(LOGIN, payload(password="not-the-password"))
    entry = AuditEntry.objects.get(action="auth.login_failed")
    assert entry.owner_id == owner.id
    assert entry.detail["consecutive_failures"] == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("email"),
        lambda p: p.pop("password"),
        lambda p: p.pop("device"),
        lambda p: p["device"].pop("platform"),
        lambda p: p["device"].pop("name"),
        lambda p: p["device"].update(platform="playstation"),
        lambda p: p["device"].update(name="x" * 101),
        lambda p: p.update(email="not-an-email"),
        lambda p: p.update(password="x" * 257),
        lambda p: p["device"].update(id="not-a-uuid"),
    ],
)
def test_invalid_payload_is_a_validation_error(api_client, owner, mutate):
    data = payload()
    mutate(data)
    response = api_client.post(LOGIN, data)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_lockout_after_repeated_failures_blocks_even_the_right_password(
    api_client, owner, settings
):
    settings.LOGIN_MAX_FAILURES = 3
    for _ in range(3):
        assert api_client.post(LOGIN, payload(password="bad-password-value")).status_code == 401
    locked = api_client.post(LOGIN, payload())
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "rate_limited"
    assert int(locked["Retry-After"]) > 0
    assert "auth.login_locked" in audit_actions()


def test_successful_login_resets_the_failure_counter(api_client, owner, settings):
    settings.LOGIN_MAX_FAILURES = 3
    for _ in range(2):
        api_client.post(LOGIN, payload(password="bad-password-value"))
    assert api_client.post(LOGIN, payload()).status_code == 200
    for _ in range(2):
        assert api_client.post(LOGIN, payload(password="bad-password-value")).status_code == 401
    assert api_client.post(LOGIN, payload()).status_code == 200


def test_ip_throttle_applies_across_different_accounts(api_client):
    codes = [
        api_client.post(LOGIN, payload(email=f"user{i}@example.com")).status_code for i in range(12)
    ]
    assert codes[:10] == [401] * 10
    assert codes[10:] == [429, 429]


def test_spoofed_forwarded_header_does_not_evade_the_ip_throttle(api_client):
    codes = [
        api_client.post(
            LOGIN, payload(email=f"user{i}@example.com"), HTTP_X_FORWARDED_FOR=f"198.51.100.{i}"
        ).status_code
        for i in range(11)
    ]
    assert codes[-1] == 429


def test_login_with_existing_device_id_reuses_the_device(api_client, owner):
    existing = DeviceFactory(owner=owner, name="Old name")
    response = api_client.post(LOGIN, payload(id=str(existing.id), name="New name"))
    assert response.status_code == 200
    assert response.json()["device"]["id"] == str(existing.id)
    existing.refresh_from_db()
    assert existing.name == "New name"
    assert Device.objects.filter(owner=owner).count() == 1


def test_login_never_reuses_another_owners_device(api_client, owner):
    foreign = DeviceFactory()
    response = api_client.post(LOGIN, payload(id=str(foreign.id)))
    assert response.status_code == 200
    assert response.json()["device"]["id"] != str(foreign.id)


def test_login_does_not_reactivate_a_revoked_device(api_client, owner):
    from django.utils import timezone

    revoked = DeviceFactory(owner=owner, revoked_at=timezone.now())
    response = api_client.post(LOGIN, payload(id=str(revoked.id)))
    assert response.status_code == 200
    assert response.json()["device"]["id"] != str(revoked.id)
    revoked.refresh_from_db()
    assert revoked.revoked_at is not None


def test_bad_authorization_header_does_not_break_login(api_client, owner):
    api_client.credentials(HTTP_AUTHORIZATION="Bearer garbage.token.value")
    assert api_client.post(LOGIN, payload()).status_code == 200


def test_login_requires_post(api_client):
    assert api_client.get(LOGIN).status_code == 405


def test_failed_login_never_stores_the_attempted_email_in_the_audit_log(api_client):
    # People sometimes type their password into the email field; the audit log is
    # append-only, so the raw attempt must never reach it.
    typed = "Hunter2-not-an-email@example.com"
    api_client.post(LOGIN, payload(email=typed, password="whatever"))
    entry = AuditEntry.objects.get(action="auth.login_failed")
    assert typed.lower() not in str(entry.detail)
    assert len(entry.detail["attempted_email_sha256"]) == 16
    assert entry.owner_id is None


def test_locked_login_audit_entry_has_no_raw_email(api_client, settings):
    settings.LOGIN_MAX_FAILURES = 1
    typed = "locked-user@example.com"
    api_client.post(LOGIN, payload(email=typed, password="x"))
    api_client.post(LOGIN, payload(email=typed, password="x"))
    entry = AuditEntry.objects.get(action="auth.login_locked")
    assert typed not in str(entry.detail)

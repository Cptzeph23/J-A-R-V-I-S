import uuid

import pytest

from apps.audit.recorder import MAX_STRING_LENGTH, REDACTED, record, scrub

pytestmark = pytest.mark.django_db


def test_record_persists_fields():
    owner = uuid.uuid4()
    device = uuid.uuid4()
    entry = record(
        "auth.login_succeeded",
        owner_id=owner,
        target_type="device",
        target_id=device,
        device_id=str(device),
        ip="203.0.113.9",
        detail={"platform": "android"},
    )
    entry.refresh_from_db()
    assert entry.owner_id == owner
    assert entry.device_id == device
    assert entry.target_id == str(device)
    assert entry.ip == "203.0.113.9"
    assert entry.detail == {"platform": "android"}
    assert entry.actor == "user"


@pytest.mark.parametrize("action", ["login", "Auth.Login", "auth.", ".login", "auth login", ""])
def test_invalid_action_is_rejected(action):
    with pytest.raises(ValueError, match="Invalid audit action"):
        record(action)


def test_invalid_actor_is_rejected():
    with pytest.raises(ValueError, match="Invalid audit actor"):
        record("auth.login_failed", actor="root")


def test_sensitive_keys_are_redacted_recursively():
    cleaned = scrub(
        {
            "email": "a@example.com",
            "password": "hunter2",
            "Refresh_Token": "abc",
            "nested": {"api_key": "k", "ok": 1, "list": [{"Authorization": "Bearer x", "n": 2}]},
        }
    )
    assert cleaned["email"] == "a@example.com"
    assert cleaned["password"] == REDACTED
    assert cleaned["Refresh_Token"] == REDACTED
    assert cleaned["nested"]["api_key"] == REDACTED
    assert cleaned["nested"]["ok"] == 1
    assert cleaned["nested"]["list"][0]["Authorization"] == REDACTED
    assert cleaned["nested"]["list"][0]["n"] == 2


def test_record_redacts_before_persisting():
    entry = record("auth.login_failed", detail={"password": "hunter2", "email": "a@example.com"})
    entry.refresh_from_db()
    assert entry.detail["password"] == REDACTED
    assert "hunter2" not in str(entry.detail)


def test_long_strings_and_deep_nesting_are_bounded():
    assert len(scrub("x" * 5000)) == MAX_STRING_LENGTH + 3
    deep: dict = {}
    cursor = deep
    for _ in range(20):
        cursor["n"] = {}
        cursor = cursor["n"]
    assert "[truncated]" in str(scrub(deep))


def test_non_json_values_are_stringified():
    value = uuid.uuid4()
    assert scrub({"id": value}) == {"id": str(value)}

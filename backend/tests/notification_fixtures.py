"""Fixtures for the events and notifications tests: a fake push provider and registered devices."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from apps.accounts.models import User
from apps.devices.models import Device
from apps.devices.services import push
from apps.notifications import providers
from apps.notifications.models import NotificationDelivery
from apps.notifications.providers.fake import FakePush
from tests.factories import DeviceFactory


@pytest.fixture
def fake_push(monkeypatch: pytest.MonkeyPatch) -> FakePush:
    """Replaces the FCM provider for the test; APNs keeps its real (unconfigured) adapter."""
    fake = FakePush()
    monkeypatch.setitem(providers._PROVIDERS, "fcm", fake)
    return fake


def retry_time(delivery: NotificationDelivery) -> datetime:
    """When a failed delivery will next be tried (it must have one)."""
    delivery.refresh_from_db()
    assert delivery.next_attempt_at is not None
    return delivery.next_attempt_at


def register_device(owner: User, token: str | None = None, provider: str = "fcm") -> Device:
    device = DeviceFactory(owner=owner)
    push.register(device, provider=provider, token=token or f"tok-{uuid.uuid4().hex}-{'x' * 20}")
    return device

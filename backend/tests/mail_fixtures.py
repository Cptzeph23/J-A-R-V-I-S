"""A fake Google and a helper that connects an owner through the real OAuth service."""

from __future__ import annotations

import urllib.parse

import pytest

from apps.accounts.models import User
from apps.integrations.models import Integration, Kind, Provider
from apps.integrations.providers import registry
from apps.integrations.providers.fake import FakeProvider
from apps.integrations.services import oauth


@pytest.fixture
def fake_google(monkeypatch: pytest.MonkeyPatch, settings) -> FakeProvider:
    settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"
    settings.GOOGLE_OAUTH_REDIRECT_URI = "https://app.example.com/oauth/callback"
    fake = FakeProvider()
    monkeypatch.setitem(registry._OAUTH, "google", lambda: fake)
    monkeypatch.setitem(registry._MAIL, "google", lambda: fake)
    monkeypatch.setitem(registry._CALENDAR, "google", lambda: fake)
    return fake


def state_from(url: str) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]


def connect(owner: User, code: str = "auth-code", kind: str = Kind.MAIL) -> Integration:
    url = oauth.start_authorization(owner, provider=Provider.GOOGLE, kind=kind)
    return oauth.complete_authorization(state=state_from(url), code=code)


def make_connected(owner: User, fake: FakeProvider, *, sync: bool = True) -> Integration:
    """For tests that need a connected, optionally synced mailbox without the HTTP flow."""
    from apps.mailbox.sync import run_sync

    integration = connect(owner)
    if sync:
        run_sync(integration.pk)
        integration.refresh_from_db()
    return integration


def make_calendar_connected(owner: User, fake: FakeProvider, *, sync: bool = True) -> Integration:
    """A connected calendar, optionally synced once, for tests that need one without HTTP."""
    from apps.scheduling.sync import run_sync

    integration = connect(owner, kind=Kind.CALENDAR)
    if sync:
        run_sync(integration.pk)
        integration.refresh_from_db()
    return integration


def connect_for_idor(owner: User, kind: str = Kind.MAIL) -> Integration:
    """Connect through the real flow using a private fake, for the cross-owner harness."""
    from unittest import mock

    fake = FakeProvider(email=f"{owner.pk.hex[:8]}@example.com")
    with (
        mock.patch.dict(registry._OAUTH, {"google": lambda: fake}),
        mock.patch.dict(registry._MAIL, {"google": lambda: fake}),
        mock.patch.dict(registry._CALENDAR, {"google": lambda: fake}),
        mock.patch.multiple(
            "django.conf.settings",
            GOOGLE_OAUTH_CLIENT_ID="client-id",
            GOOGLE_OAUTH_CLIENT_SECRET="client-secret",
            GOOGLE_OAUTH_REDIRECT_URI="https://app.example.com/oauth/callback",
        ),
    ):
        return connect(owner, kind=kind)

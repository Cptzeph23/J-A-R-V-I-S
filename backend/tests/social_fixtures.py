"""A fake platform and helpers that connect and publish through the real services."""

from __future__ import annotations

import urllib.parse

import pytest

from apps.accounts.models import User
from apps.social.models import SocialAccount, SocialPost
from apps.social.providers import registry
from apps.social.providers.fake import FakePlatform
from apps.social.services import accounts, posts


@pytest.fixture
def fake_fb(monkeypatch: pytest.MonkeyPatch, settings) -> FakePlatform:
    settings.META_APP_ID = "app-id"
    settings.META_APP_SECRET = "app-secret"
    settings.META_REDIRECT_URI = "https://app.example.com/api/v1/social/oauth/callback"
    fake = FakePlatform()
    monkeypatch.setitem(registry._PROVIDERS, "facebook", lambda: fake)
    return fake


def state_from(url: str) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]


def connect_page(owner: User, code: str = "auth-code") -> SocialAccount:
    url = accounts.start(owner)
    outcome, account = accounts.complete(state=state_from(url), code=code)
    assert outcome == "connected"
    assert account is not None
    return account


def approved_post(owner: User, text: str = "Hello from the Page", link: str = "") -> SocialPost:
    post = posts.create_draft(owner, text=text, link=link)
    return posts.approve(owner, post.pk, echoed_sha256=post.content_sha256)


def queued_post(owner: User, text: str = "Hello from the Page") -> SocialPost:
    post = approved_post(owner, text)
    return posts.request_publish(owner, post.pk)


def connect_for_idor(owner: User) -> SocialAccount:
    from unittest import mock

    fake = FakePlatform()
    with (
        mock.patch.dict(registry._PROVIDERS, {"facebook": lambda: fake}),
        mock.patch.multiple(
            "django.conf.settings",
            META_APP_ID="app-id",
            META_APP_SECRET="app-secret",
            META_REDIRECT_URI="https://app.example.com/cb",
        ),
    ):
        return connect_page(owner)

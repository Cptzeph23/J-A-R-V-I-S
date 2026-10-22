"""Authenticated-client fixtures available to every test in the project.

`client_a` / `client_b` belong to two different owners and are the building blocks of
the cross-owner (IDOR) tests: anything owned by A must be invisible to B.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.services import auth
from apps.devices.models import Device
from tests.factories import DEFAULT_PASSWORD, UserFactory


class AuthedClient(APIClient):
    user: User
    pair: auth.TokenPair

    @property
    def device(self) -> Device:
        return self.pair.device


def authenticate(
    user: User, *, name: str = "Test phone", platform: str = "android"
) -> AuthedClient:
    """Log `user` in through the service layer (bypassing HTTP throttles) and return a client."""
    pair = auth.login(
        email=user.email, password=DEFAULT_PASSWORD, platform=platform, device_name=name
    )
    client = AuthedClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {pair.access_token}")
    client.user = user
    client.pair = pair
    return client


@pytest.fixture
def owner_a(db) -> User:
    return UserFactory(email="a@example.com")


@pytest.fixture
def owner_b(db) -> User:
    return UserFactory(email="b@example.com")


@pytest.fixture
def client_a(owner_a: User) -> AuthedClient:
    return authenticate(owner_a, name="A phone")


@pytest.fixture
def client_b(owner_b: User) -> AuthedClient:
    return authenticate(owner_b, name="B phone")


@pytest.fixture
def make_client(db) -> Callable[..., AuthedClient]:
    def _make(**user_kwargs: Any) -> AuthedClient:
        return authenticate(UserFactory(**user_kwargs))

    return _make

"""Registry of every endpoint that serves owner-scoped data.

Adding an `OwnedModel` without registering it here fails `test_idor_harness.py`, so a new
model cannot ship without a declared cross-owner test. Each `OwnedEndpoint` names a URL
template (`{id}` is the object's primary key), the HTTP methods to attack, and a factory
that creates one instance for a given owner.

For a model with no API surface yet, register `NoEndpoint("reason")`: the reason is
reviewed and must be revisited when an endpoint is added.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from apps.accounts.models import User
from tests.factories import DeviceFactory


@dataclass(frozen=True)
class OwnedEndpoint:
    url: str
    methods: tuple[str, ...]
    create: Callable[[User], Any]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NoEndpoint:
    reason: str


REGISTRY: dict[str, list[OwnedEndpoint] | NoEndpoint] = {
    "devices.Device": [
        OwnedEndpoint(
            url="/api/v1/devices/{id}",
            methods=("patch", "delete"),
            create=lambda owner: DeviceFactory(owner=owner),
            payload={"name": "hijacked"},
        )
    ],
    "devices.RefreshToken": NoEndpoint(
        "Never exposed through the API; reachable only via the auth service by token hash."
    ),
}

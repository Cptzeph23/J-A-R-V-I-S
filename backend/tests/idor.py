"""Assertions for the cross-owner and unauthenticated access contract.

Contract (docs/04-SRS.md SRS-SEC-03): another owner's object is indistinguishable from a
missing one (404, same body shape), and anonymous callers get 401.
"""

from __future__ import annotations

from typing import Any

from rest_framework.test import APIClient


def request(client: APIClient, method: str, url: str, **kwargs: Any):
    return getattr(client, method.lower())(url, **kwargs)


def assert_foreign_access_hidden(client: APIClient, method: str, url: str, **kwargs: Any) -> None:
    response = request(client, method, url, **kwargs)
    assert response.status_code == 404, (
        f"{method.upper()} {url} as a foreign owner returned {response.status_code}, expected 404"
    )
    assert response.json()["error"]["code"] == "not_found"


def assert_unauthenticated_rejected(method: str, url: str, **kwargs: Any) -> None:
    response = request(APIClient(), method, url, **kwargs)
    assert response.status_code == 401, (
        f"{method.upper()} {url} without credentials returned {response.status_code}, expected 401"
    )
    assert response.json()["error"]["code"] == "unauthenticated"

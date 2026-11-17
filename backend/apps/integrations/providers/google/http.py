"""HTTPS to Google over the standard library.

STATUS: written from Google's documented REST shapes; NOT yet exercised against the live
service (this environment cannot reach it). Tests use an injected opener. Run one real
connect-and-sync before relying on it.

Properties: only allow-listed Google hosts over https, redirects refused (a token is never
forwarded elsewhere), bounded response size, and no URL, header, request or response body in
any exception or log line.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from ..base import (
    BadAuthorization,
    EventAlreadyExists,
    EventChanged,
    GrantRevoked,
    HistoryExpired,
    ProviderError,
    ProviderRateLimited,
    ProviderUnavailable,
)

ALLOWED_HOSTS = frozenset(
    {"accounts.google.com", "oauth2.googleapis.com", "gmail.googleapis.com", "www.googleapis.com"}
)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 20


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


Opener = Callable[..., Any]


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


def check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ProviderError("Refusing to call a host outside the Google allow-list.")


def call(
    method: str,
    url: str,
    *,
    bearer: str = "",
    form: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    opener: Opener | None = None,
) -> dict[str, Any]:
    """Returns the decoded JSON object ({} for an empty body). Raises ProviderError subclasses."""
    check_url(url)
    headers = {"Accept": "application/json", "User-Agent": "jarvis-assistant"}
    data: bytes | None = None
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    headers.update(extra_headers or {})
    request = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
    try:
        response = (opener or _default_opener)(request, TIMEOUT_SECONDS)
        with response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise _map_status(exc.code, exc.headers.get("Retry-After", ""), _error_name(exc)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderUnavailable("network failure") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderUnavailable("response too large")
    if not raw.strip():
        return {}
    try:
        decoded = json.loads(raw)
    except ValueError:
        raise ProviderUnavailable("unreadable response") from None
    if not isinstance(decoded, dict):
        raise ProviderUnavailable("unexpected response shape")
    return decoded


def _error_name(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read(4096) or b"{}")
    except (ValueError, OSError):
        return ""
    value = body.get("error") if isinstance(body, dict) else ""
    return value if isinstance(value, str) else ""


def _map_status(status: int, retry_after: str, error_name: str) -> ProviderError:
    if status == 400 and error_name == "invalid_grant":
        return GrantRevoked("grant is no longer valid")
    if status == 401:
        return GrantRevoked("access was rejected")
    if status in {404, 410}:
        return HistoryExpired("not found")  # callers of other APIs read this as "no such item"
    if status == 409:
        return EventAlreadyExists("already exists")
    if status == 412:
        return EventChanged("changed since it was read")
    if status == 429 or (
        status == 403 and error_name in {"rateLimitExceeded", "userRateLimitExceeded"}
    ):
        seconds = int(retry_after) if retry_after.isdigit() else 0
        return ProviderRateLimited(min(seconds, 3600))
    if status >= 500:
        return ProviderUnavailable(f"provider error {status}")
    if status in {400, 403}:
        return BadAuthorization(f"request refused ({status})")
    return ProviderError(f"unexpected status {status}")

"""Facebook Pages over the Graph API, standard library only.

STATUS: written from Meta's documented request shapes (Graph API version in
`settings.META_GRAPH_VERSION`); NOT exercised against the live service (this environment
cannot reach it). Tests use an injected opener. Run one real connect, one post and one reconcile
against a test Page before relying on it.

Properties: only allow-listed Meta hosts over https, redirects refused, bounded responses, and
no URL, token, request or response body in any exception or log line.

The Graph API has no idempotency key for creating a post. A request that fails after it was
sent (timeout, connection loss, 5xx, transient error codes) is therefore `OutcomeUnknown`.
Only a parsed refusal from the platform (a 4xx with an error object that is not transient) is
`PublishRejected`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from django.conf import settings

from .base import (
    NotConfigured,
    OutcomeUnknown,
    PageGrant,
    PublishRejected,
    RateLimited,
    ReadFailed,
    RemoteMetrics,
    RemotePost,
    SocialProviderError,
    TokenRejected,
)

ALLOWED_HOSTS = frozenset({"graph.facebook.com", "www.facebook.com"})
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 20
# The complete set of permissions ever requested (tests pin this).
SCOPES = ("pages_show_list", "pages_manage_posts", "pages_read_engagement")
RATE_LIMIT_CODES = frozenset({4, 17, 32, 613, 80001})
TRANSIENT_CODES = frozenset({1, 2})  # "unknown error / service temporarily unavailable"
TOKEN_CODES = frozenset({102, 190})

Opener = Callable[..., Any]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


class _HttpFailure(Exception):
    """A response that was received and was an error (as opposed to no response at all)."""

    def __init__(self, status: int, code: int, subcode: int, transient: bool) -> None:
        super().__init__(f"status {status}")
        self.status, self.code, self.subcode, self.transient = status, code, subcode, transient


class _NoResponse(Exception):
    """Nothing usable came back: the request may or may not have been processed."""


def _check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise SocialProviderError("Refusing to call a host outside the Meta allow-list.")


def _error_fields(exc: urllib.error.HTTPError) -> tuple[int, int, bool]:
    try:
        body = json.loads(exc.read(8192) or b"{}")
    except (ValueError, OSError):
        return 0, 0, False
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return 0, 0, False
    code, sub = err.get("code"), err.get("error_subcode")
    return (
        code if isinstance(code, int) else 0,
        sub if isinstance(sub, int) else 0,
        err.get("is_transient") is True,
    )


def _request(
    method: str, url: str, *, form: dict[str, str] | None, opener: Opener | None
) -> dict[str, Any]:
    _check_url(url)
    headers = {"Accept": "application/json", "User-Agent": "jarvis-assistant"}
    data: bytes | None = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
    try:
        response = (opener or _default_opener)(request, TIMEOUT_SECONDS)
        with response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code, sub, transient = _error_fields(exc)
        raise _HttpFailure(exc.code, code, sub, transient) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise _NoResponse from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _NoResponse
    try:
        decoded = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise _NoResponse from None
    if not isinstance(decoded, dict):
        raise _NoResponse
    return decoded


def _is_unknown(failure: _HttpFailure) -> bool:
    """A failure after which the post may still exist."""
    return (
        failure.status >= 500
        or failure.code in TRANSIENT_CODES
        or failure.transient
        or failure.code == 0  # an error we could not parse: do not assume it was refused
    )


class FacebookPages:
    def __init__(self, opener: Opener | None = None) -> None:
        self._opener = opener

    @staticmethod
    def _base() -> str:
        return f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}"

    @staticmethod
    def _app() -> tuple[str, str]:
        app_id, secret = settings.META_APP_ID, settings.META_APP_SECRET
        if not app_id or not secret:
            raise NotConfigured("Facebook is not configured.")
        return app_id, secret

    def build_authorization_url(self, *, state: str, redirect_uri: str) -> str:
        app_id, _ = self._app()
        query = urllib.parse.urlencode(
            {
                "client_id": app_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "response_type": "code",
                "scope": ",".join(SCOPES),
            }
        )
        return f"https://www.facebook.com/{settings.META_GRAPH_VERSION}/dialog/oauth?{query}"

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self._base()}/{path}?{urllib.parse.urlencode(params)}"
        return _request("GET", url, form=None, opener=self._opener)

    def exchange_code(self, *, code: str, redirect_uri: str) -> list[PageGrant]:
        app_id, secret = self._app()
        try:
            short = self._get(
                "oauth/access_token",
                {
                    "client_id": app_id,
                    "client_secret": secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
            ).get("access_token")
            if not isinstance(short, str) or not short:
                raise TokenRejected("no token")
            long_lived = self._get(
                "oauth/access_token",
                {
                    "grant_type": "fb_exchange_token",
                    "client_id": app_id,
                    "client_secret": secret,
                    "fb_exchange_token": short,
                },
            ).get("access_token")
            if not isinstance(long_lived, str) or not long_lived:
                raise TokenRejected("no long-lived token")
            accounts = self._get(
                "me/accounts",
                {
                    "fields": "id,name,access_token,tasks",
                    "limit": "100",
                    "access_token": long_lived,
                },
            )
        except _HttpFailure:
            raise TokenRejected("login could not be completed") from None
        except _NoResponse:
            raise ReadFailed("network failure") from None
        return _grants(accounts.get("data"))

    def publish(self, *, page_id: str, token: str, text: str, link: str) -> str:
        form = {"message": text, "access_token": token}
        if link:
            form["link"] = link
        try:
            body = _request(
                "POST",
                f"{self._base()}/{urllib.parse.quote(page_id, safe='')}/feed",
                form=form,
                opener=self._opener,
            )
        except _NoResponse:
            raise OutcomeUnknown("no usable response") from None
        except _HttpFailure as failure:
            raise _publish_error(failure) from None
        post_id = body.get("id")
        if not isinstance(post_id, str) or not post_id:
            raise OutcomeUnknown("response had no post id")
        return post_id

    def recent_posts(
        self, *, page_id: str, token: str, since: datetime, limit: int = 25
    ) -> list[RemotePost]:
        try:
            body = self._get(
                f"{urllib.parse.quote(page_id, safe='')}/posts",
                {
                    "fields": "id,message,created_time",
                    "limit": str(limit),
                    "since": str(int(since.timestamp())),
                    "access_token": token,
                },
            )
        except _HttpFailure as failure:
            if failure.code in TOKEN_CODES or failure.status == 401:
                raise TokenRejected("token rejected") from None
            raise ReadFailed("read refused") from None
        except _NoResponse:
            raise ReadFailed("network failure") from None
        out: list[RemotePost] = []
        for row in body.get("data") or []:
            if not isinstance(row, dict):
                continue
            post_id, message, created = row.get("id"), row.get("message"), row.get("created_time")
            if isinstance(post_id, str) and isinstance(message, str) and isinstance(created, str):
                parsed = _parse_time(created)
                if parsed is not None:
                    out.append(RemotePost(post_id, message, parsed))
        return out

    def metrics(self, *, post_id: str, token: str) -> RemoteMetrics:
        try:
            body = self._get(
                urllib.parse.quote(post_id, safe=""),
                {
                    "fields": "reactions.summary(total_count).limit(0),"
                    "comments.summary(total_count).limit(0),shares",
                    "access_token": token,
                },
            )
        except _HttpFailure as failure:
            if failure.code in TOKEN_CODES or failure.status == 401:
                raise TokenRejected("token rejected") from None
            raise ReadFailed("read refused") from None
        except _NoResponse:
            raise ReadFailed("network failure") from None
        return RemoteMetrics(
            reactions=_count(body.get("reactions")),
            comments=_count(body.get("comments")),
            shares=_shares(body.get("shares")),
        )


def _publish_error(failure: _HttpFailure) -> SocialProviderError:
    if failure.code in TOKEN_CODES or failure.status == 401:
        return TokenRejected("token rejected")
    if failure.code in RATE_LIMIT_CODES or failure.status == 429:
        return RateLimited("rate limited")
    if _is_unknown(failure):
        return OutcomeUnknown(f"unconfirmed outcome ({failure.status})")
    if failure.code in {10, 200} or 200 <= failure.code <= 299:
        return PublishRejected("permission_denied")
    if failure.code == 368:
        return PublishRejected("blocked_by_platform")
    return PublishRejected("publish_rejected")


def _grants(rows: object) -> list[PageGrant]:
    out: list[PageGrant] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        page_id, name, token, tasks = (
            row.get("id"),
            row.get("name"),
            row.get("access_token"),
            row.get("tasks"),
        )
        if not (isinstance(page_id, str) and isinstance(name, str) and isinstance(token, str)):
            continue
        if isinstance(tasks, list) and "CREATE_CONTENT" not in tasks:
            continue  # this login cannot post to that Page
        out.append(PageGrant(page_id=page_id, name=name[:200], token=token))
    return out


def _parse_time(value: str) -> datetime | None:
    try:
        # Graph returns e.g. 2026-11-26T09:00:00+0000 (no colon in the offset).
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z").astimezone(UTC)
    except ValueError:
        return None


def _count(node: object) -> int:
    summary = node.get("summary") if isinstance(node, dict) else None
    total = summary.get("total_count") if isinstance(summary, dict) else None
    return total if isinstance(total, int) and total >= 0 else 0


def _shares(node: object) -> int:
    count = node.get("count") if isinstance(node, dict) else None
    return count if isinstance(count, int) and count >= 0 else 0

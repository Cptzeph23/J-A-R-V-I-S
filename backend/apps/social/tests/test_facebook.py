"""The Facebook adapter against scripted HTTP responses (never the network)."""

import io
import json
import urllib.error
import urllib.parse
from datetime import UTC, datetime
from email.message import Message

import pytest

from apps.social.providers import base, facebook
from apps.social.providers.facebook import FacebookPages


class Resp:
    def __init__(self, body: object, status: int = 200) -> None:
        self._raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status

    def read(self, n: int = -1) -> bytes:
        return self._raw[:n] if n >= 0 else self._raw

    def __enter__(self) -> "Resp":
        return self

    def __exit__(self, *a: object) -> None:
        return None


def http_error(status: int, body: object) -> urllib.error.HTTPError:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return urllib.error.HTTPError(
        "https://graph.facebook.com/x", status, "err", Message(), io.BytesIO(raw)
    )


class Script:
    """An opener that returns or raises scripted items and records every request."""

    def __init__(self, *items: object) -> None:
        self.items = list(items)
        self.requests: list[tuple[str, str, bytes | None]] = []

    def __call__(self, request, timeout):
        self.requests.append((request.get_method(), request.full_url, request.data))
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture(autouse=True)
def configured(settings):
    settings.META_APP_ID = "app-id"
    settings.META_APP_SECRET = "app-secret"
    settings.META_GRAPH_VERSION = "v26.0"


def client(*items: object) -> tuple[FacebookPages, Script]:
    script = Script(*items)
    return FacebookPages(opener=script), script


def err(code: int, status: int = 400, **extra: object) -> urllib.error.HTTPError:
    return http_error(status, {"error": {"code": code, "message": "secret detail", **extra}})


def test_authorization_url_requests_only_the_pinned_permissions_and_a_state():
    url = FacebookPages().build_authorization_url(state="st", redirect_uri="https://x/cb")
    parsed = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(parsed.query)
    assert parsed.hostname == "www.facebook.com"
    assert parsed.path == "/v26.0/dialog/oauth"
    assert q["scope"] == ["pages_show_list,pages_manage_posts,pages_read_engagement"]
    assert q["state"] == ["st"]
    assert q["response_type"] == ["code"]
    assert "app-secret" not in url


def test_unconfigured_app_is_reported():
    from django.test import override_settings

    with override_settings(META_APP_ID=""), pytest.raises(base.NotConfigured):
        FacebookPages().build_authorization_url(state="s", redirect_uri="https://x")


def test_exchange_returns_only_pages_the_login_can_post_to():
    fb, script = client(
        Resp({"access_token": "short"}),
        Resp({"access_token": "long"}),
        Resp(
            {
                "data": [
                    {"id": "1", "name": "Mine", "access_token": "t1", "tasks": ["CREATE_CONTENT"]},
                    {"id": "2", "name": "Read only", "access_token": "t2", "tasks": ["ANALYZE"]},
                    {"id": "3", "name": "No tasks listed", "access_token": "t3"},
                    {"id": "4", "name": "no token"},
                    "junk",
                ]
            }
        ),
    )
    grants = fb.exchange_code(code="c", redirect_uri="https://x/cb")
    assert [(g.page_id, g.token) for g in grants] == [("1", "t1"), ("3", "t3")]
    second = urllib.parse.parse_qs(urllib.parse.urlparse(script.requests[1][1]).query)
    assert second["grant_type"] == ["fb_exchange_token"]
    assert "t1" not in repr(grants)  # tokens stay out of reprs and logs


def test_exchange_failures_do_not_leak_details():
    fb, _ = client(err(100))
    with pytest.raises(base.TokenRejected) as info:
        fb.exchange_code(code="c", redirect_uri="https://x")
    assert "secret detail" not in str(info.value)
    assert "app-secret" not in str(info.value)
    fb, _ = client(urllib.error.URLError("dns"))
    with pytest.raises(base.ReadFailed):
        fb.exchange_code(code="c", redirect_uri="https://x")


def test_publish_posts_message_and_link_in_the_body_not_the_url():
    fb, script = client(Resp({"id": "123_456"}))
    assert (
        fb.publish(page_id="123", token="PT", text="Hi & bye", link="https://e.com/a") == "123_456"
    )
    method, url, data = script.requests[0]
    assert method == "POST"
    assert url == "https://graph.facebook.com/v26.0/123/feed"
    assert "PT" not in url
    assert data is not None
    form = urllib.parse.parse_qs(data.decode())
    assert form == {"message": ["Hi & bye"], "access_token": ["PT"], "link": ["https://e.com/a"]}


def test_publish_without_link_omits_it():
    fb, script = client(Resp({"id": "1_2"}))
    fb.publish(page_id="1", token="t", text="x", link="")
    body = script.requests[0][2]
    assert body is not None
    assert "link" not in urllib.parse.parse_qs(body.decode())


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (err(190, 401), base.TokenRejected),
        (err(102), base.TokenRejected),
        (err(4, 400), base.RateLimited),
        (err(32, 400), base.RateLimited),
        (err(613, 400), base.RateLimited),
        (http_error(429, {"error": {"code": 0}}), base.RateLimited),
        (err(100), base.PublishRejected),
        (err(368), base.PublishRejected),
        (err(200), base.PublishRejected),
        (err(10), base.PublishRejected),
        # The post may exist after these: never reported as "not published".
        (err(1), base.OutcomeUnknown),
        (err(2), base.OutcomeUnknown),
        (err(100, 400, is_transient=True), base.OutcomeUnknown),
        (http_error(500, {"error": {"code": 100}}), base.OutcomeUnknown),
        (http_error(502, b"<html>bad gateway</html>"), base.OutcomeUnknown),
        (http_error(400, b"not json"), base.OutcomeUnknown),
        (urllib.error.URLError("reset"), base.OutcomeUnknown),
        (TimeoutError(), base.OutcomeUnknown),
        (Resp(b"not json"), base.OutcomeUnknown),
        (Resp({"no": "id"}), base.OutcomeUnknown),
        (Resp([1, 2]), base.OutcomeUnknown),
    ],
)
def test_publish_failures_are_classified_by_whether_the_post_may_exist(failure, expected):
    fb, _ = client(failure)
    with pytest.raises(expected) as info:
        fb.publish(page_id="1", token="t", text="x", link="")
    assert type(info.value) is expected or isinstance(info.value, expected)
    assert "secret detail" not in str(info.value)


def test_rejection_reasons_are_stable_codes():
    for code, reason in [
        (200, "permission_denied"),
        (368, "blocked_by_platform"),
        (100, "publish_rejected"),
    ]:
        fb, _ = client(err(code))
        with pytest.raises(base.PublishRejected) as info:
            fb.publish(page_id="1", token="t", text="x", link="")
        assert info.value.code == reason


def test_oversized_response_is_unknown_not_success():
    fb, _ = client(Resp(b"{" + b" " * (facebook.MAX_RESPONSE_BYTES + 10) + b"}"))
    with pytest.raises(base.OutcomeUnknown):
        fb.publish(page_id="1", token="t", text="x", link="")


def test_redirects_are_refused():
    handler = facebook._NoRedirect()
    assert handler.redirect_request() is None  # type: ignore[func-returns-value]


def test_hosts_outside_the_allow_list_are_refused(monkeypatch):
    fb = FacebookPages(opener=Script())
    monkeypatch.setattr(
        FacebookPages, "_base", staticmethod(lambda: "https://evil.example.com/v26.0")
    )
    with pytest.raises(base.SocialProviderError):
        fb.publish(page_id="1", token="t", text="x", link="")
    with pytest.raises(base.SocialProviderError):
        facebook._check_url("http://graph.facebook.com/x")


def test_page_ids_cannot_inject_path_segments():
    fb, script = client(Resp({"id": "1_2"}))
    fb.publish(page_id="1/../me", token="t", text="x", link="")
    assert script.requests[0][1] == "https://graph.facebook.com/v26.0/1%2F..%2Fme/feed"


def test_recent_posts_parse_and_skip_malformed_rows():
    fb, script = client(
        Resp(
            {
                "data": [
                    {"id": "1_1", "message": "hello", "created_time": "2026-11-26T09:00:00+0000"},
                    {"id": "1_2", "created_time": "2026-11-26T09:00:00+0000"},
                    {"id": "1_3", "message": "bad time", "created_time": "yesterday"},
                    "junk",
                ]
            }
        )
    )
    got = fb.recent_posts(page_id="1", token="t", since=datetime(2026, 11, 26, 8, tzinfo=UTC))
    assert [(p.post_id, p.message) for p in got] == [("1_1", "hello")]
    assert got[0].created_at == datetime(2026, 11, 26, 9, tzinfo=UTC)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(script.requests[0][1]).query)
    assert q["since"] == [str(int(datetime(2026, 11, 26, 8, tzinfo=UTC).timestamp()))]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (err(190, 401), base.TokenRejected),
        (err(100), base.ReadFailed),
        (urllib.error.URLError("x"), base.ReadFailed),
    ],
)
def test_read_failures(failure, expected):
    fb, _ = client(failure, failure)
    with pytest.raises(expected):
        fb.recent_posts(page_id="1", token="t", since=datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(expected):
        fb.metrics(post_id="1_1", token="t")


def test_metrics_parse_counts_and_missing_shares_are_zero():
    fb, _ = client(
        Resp(
            {
                "reactions": {"summary": {"total_count": 7}},
                "comments": {"summary": {"total_count": 2}},
            }
        ),
        Resp(
            {"reactions": {"summary": {"total_count": 1}}, "comments": {}, "shares": {"count": 4}}
        ),
        Resp({"reactions": {"summary": {"total_count": -5}}, "shares": {"count": "x"}}),
    )
    a = fb.metrics(post_id="1_1", token="t")
    b = fb.metrics(post_id="1_1", token="t")
    c = fb.metrics(post_id="1_1", token="t")
    assert (a.reactions, a.comments, a.shares) == (7, 2, 0)
    assert (b.reactions, b.comments, b.shares) == (1, 0, 4)
    assert (c.reactions, c.comments, c.shares) == (0, 0, 0)

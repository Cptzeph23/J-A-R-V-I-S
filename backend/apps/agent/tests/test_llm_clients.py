"""FakeLLM behaviour and the Anthropic adapter against a mocked transport."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from apps.agent.llm.anthropic_client import API_VERSION, AnthropicClient
from apps.agent.llm.base import (
    LLMError,
    LLMMessage,
    LLMOutputError,
    LLMRequest,
    LLMTimeout,
    LLMUnavailable,
    ToolCall,
    ToolResultBlock,
    ToolSpec,
)
from apps.agent.llm.fake import FakeLLM, call, say

MODELS = {"large": "model-large", "small": "model-small"}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _client(handler):
    return AnthropicClient(
        api_key="sk-test-secret", base_url="https://api.example.test", models=MODELS, opener=handler
    )


def _request(**kw: Any) -> LLMRequest:
    defaults: dict[str, Any] = {
        "system": "sys",
        "messages": (LLMMessage("user", text="hi"),),
    }
    defaults.update(kw)
    return LLMRequest(**defaults)


class _Recorder:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.seen: tuple[Any, float] = (None, 0.0)

    def __call__(self, http_request: Any, timeout: float) -> _Response:
        self.seen = (http_request, timeout)
        return _Response(json.dumps(self.body).encode())


def _ok(body: dict[str, Any]) -> _Recorder:
    return _Recorder(body)


# ---- FakeLLM ------------------------------------------------------------------------------


def test_fake_plays_back_in_order_and_records_requests():
    fake = FakeLLM([say("one"), call("tool_a", {"x": 1})])
    assert fake.complete(_request()).text == "one"
    second = fake.complete(_request())
    assert second.tool_calls[0].name == "tool_a"
    assert len(fake.requests) == 2
    assert fake.remaining == 0


def test_fake_raises_scripted_exceptions_and_runs_callables():
    fake = FakeLLM([LLMTimeout("t"), lambda req: say(req.system)])
    with pytest.raises(LLMTimeout):
        fake.complete(_request())
    assert fake.complete(_request(system="echo")).text == "echo"


def test_fake_fails_loudly_when_the_script_runs_out():
    with pytest.raises(AssertionError):
        FakeLLM([]).complete(_request())


# ---- Anthropic adapter --------------------------------------------------------------------


def test_request_wire_format():
    opener = _ok({"content": [{"type": "text", "text": "ok"}], "usage": {}})
    tools = (ToolSpec("get_x", "d", {"type": "object", "properties": {}}),)
    messages = (
        LLMMessage("user", text="q"),
        LLMMessage("assistant", text="", tool_calls=(ToolCall("t1", "get_x", {"a": 1}),)),
        LLMMessage("user", tool_results=(ToolResultBlock("t1", "result", is_error=True),)),
    )
    _client(opener).complete(_request(messages=messages, tools=tools, tier="small", max_tokens=77))
    http_request, timeout = opener.seen
    body = json.loads(http_request.data)
    assert http_request.full_url == "https://api.example.test/v1/messages"
    assert http_request.get_method() == "POST"
    assert body["model"] == "model-small"
    assert body["max_tokens"] == 77
    assert body["system"] == "sys"
    assert body["tools"][0] == {
        "name": "get_x",
        "description": "d",
        "input_schema": {"type": "object", "properties": {}},
    }
    assert body["messages"][1]["content"] == [
        {"type": "tool_use", "id": "t1", "name": "get_x", "input": {"a": 1}}
    ]
    assert body["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "t1", "content": "result", "is_error": True}
    ]
    assert timeout == 45.0
    headers = {k.lower(): v for k, v in http_request.header_items()}
    assert headers["anthropic-version"] == API_VERSION
    assert headers["content-type"] == "application/json"
    assert "x-api-key" not in {k.lower() for k in http_request.headers}
    assert http_request.unredirected_hdrs["X-api-key"] == "sk-test-secret"


def test_no_tools_key_when_there_are_no_tools():
    opener = _ok({"content": [], "usage": {}})
    _client(opener).complete(_request())
    assert "tools" not in json.loads(opener.seen[0].data)


def test_response_parsing_text_and_tool_use():
    opener = _ok(
        {
            "content": [
                {"type": "text", "text": "Let me look. "},
                {"type": "tool_use", "id": "tu_1", "name": "get_x", "input": {"month": "2026-10"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 12, "output_tokens": 5},
        }
    )
    response = _client(opener).complete(_request())
    assert response.text == "Let me look. "
    assert response.tool_calls == (ToolCall("tu_1", "get_x", {"month": "2026-10"}),)
    assert (response.usage.input_tokens, response.usage.output_tokens) == (12, 5)
    assert response.stop_reason == "tool_use"


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"{}",
        b'{"content": "text"}',
        b'{"content": [{"type": "tool_use", "id": "1", "name": "n", "input": []}]}',
        b'{"content": [{"type": "tool_use", "name": "n", "input": {}}]}',
    ],
)
def test_malformed_responses_are_output_errors(body):
    with pytest.raises(LLMOutputError):
        _client(lambda r, timeout: _Response(body)).complete(_request())


def test_oversized_response_is_refused():
    big = b"{" + b" " * (2 * 1024 * 1024 + 10)
    with pytest.raises(LLMOutputError, match="too large"):
        _client(lambda r, timeout: _Response(big)).complete(_request())


def _http_error(code):
    def opener(r, timeout):
        raise urllib.error.HTTPError(r.full_url, code, "x", Message(), io.BytesIO(b"secret body"))

    return opener


@pytest.mark.parametrize("code", [429, 500, 503, 529])
def test_retryable_http_errors_are_unavailable(code):
    with pytest.raises(LLMUnavailable):
        _client(_http_error(code)).complete(_request())


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_client_errors_are_not_retryable_and_leak_nothing(code):
    with pytest.raises(LLMError) as info:
        _client(_http_error(code)).complete(_request())
    assert not isinstance(info.value, LLMUnavailable)
    assert "secret body" not in str(info.value)
    assert "sk-test-secret" not in str(info.value)


def test_timeouts_map_to_llm_timeout():
    def raises(r, timeout):
        raise TimeoutError("slow")

    with pytest.raises(LLMTimeout):
        _client(raises).complete(_request())

    def url_timeout(r, timeout):
        raise urllib.error.URLError(TimeoutError("slow"))

    with pytest.raises(LLMTimeout):
        _client(url_timeout).complete(_request())


def test_connection_failures_are_unavailable():
    def refused(r, timeout):
        raise urllib.error.URLError(ConnectionRefusedError())

    with pytest.raises(LLMUnavailable):
        _client(refused).complete(_request())


def test_plain_http_is_refused_except_loopback():
    with pytest.raises(ValueError, match="https"):
        AnthropicClient(api_key="k", base_url="http://api.example.test", models=MODELS)
    AnthropicClient(api_key="k", base_url="http://127.0.0.1:9999", models=MODELS)


def test_repr_hides_the_key():
    assert "sk-test-secret" not in repr(_client(_ok({"content": []})))


def test_redirects_are_refused_by_the_default_opener():
    from apps.agent.llm.anthropic_client import _NoRedirect

    handler = _NoRedirect()
    request = urllib.request.Request("https://a.test")
    result = handler.redirect_request(request, None, 302, "Found", Message(), "https://evil.test")
    assert result is None


def test_factory_without_configuration_is_unavailable(settings):
    from apps.agent.llm.factory import get_llm_client

    settings.AGENT_LLM_PROVIDER = "none"
    with pytest.raises(LLMUnavailable):
        get_llm_client()
    settings.AGENT_LLM_PROVIDER = "anthropic"
    settings.ANTHROPIC_API_KEY = ""
    with pytest.raises(LLMUnavailable):
        get_llm_client()
    settings.ANTHROPIC_API_KEY = "k"
    assert isinstance(get_llm_client(), AnthropicClient)

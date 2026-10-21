import re

import pytest

from apps.common.middleware import get_request_id

HEX32 = re.compile(r"^[0-9a-f]{32}$")


def test_generates_request_id(api_client):
    response = api_client.get("/api/v1/health/live")
    assert HEX32.match(response["X-Request-ID"])


def test_echoes_valid_inbound_request_id(api_client):
    response = api_client.get("/api/v1/health/live", headers={"X-Request-ID": "abc12345-test_1.2"})
    assert response["X-Request-ID"] == "abc12345-test_1.2"


@pytest.mark.parametrize("bad", ["x", "bad id with spaces", "a" * 65, "semi;colon;value"])
def test_replaces_malformed_request_id(api_client, bad):
    response = api_client.get("/api/v1/health/live", headers={"X-Request-ID": bad})
    assert response["X-Request-ID"] != bad
    assert HEX32.match(response["X-Request-ID"])


def test_header_present_on_error_responses(api_client):
    response = api_client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response["X-Request-ID"]
    assert response.json()["error"]["request_id"] == response["X-Request-ID"]


def test_request_id_is_cleared_after_request(api_client):
    api_client.get("/api/v1/health/live")
    assert get_request_id() is None

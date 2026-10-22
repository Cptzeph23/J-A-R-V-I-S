import pytest
from django.test import RequestFactory

from apps.common.http import client_ip

rf = RequestFactory()


def req(remote="203.0.113.5", xff=None):
    extra = {"REMOTE_ADDR": remote}
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    return rf.get("/", **extra)


def test_default_ignores_forwarded_header(settings):
    settings.TRUSTED_PROXY_COUNT = 0
    assert client_ip(req(xff="1.2.3.4")) == "203.0.113.5"


def test_one_trusted_proxy_uses_last_entry(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    # The client forged "9.9.9.9"; the proxy appended the real peer address.
    assert client_ip(req(remote="10.0.0.1", xff="9.9.9.9, 198.51.100.7")) == "198.51.100.7"


def test_two_trusted_proxies_use_second_from_right(settings):
    settings.TRUSTED_PROXY_COUNT = 2
    assert client_ip(req(xff="9.9.9.9, 198.51.100.7, 10.0.0.2")) == "198.51.100.7"


def test_header_shorter_than_chain_falls_back_to_remote(settings):
    settings.TRUSTED_PROXY_COUNT = 2
    assert client_ip(req(xff="198.51.100.7")) == "203.0.113.5"


def test_missing_header_falls_back_to_remote(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    assert client_ip(req()) == "203.0.113.5"


@pytest.mark.parametrize("bad", ["not-an-ip", "999.1.1.1", "<script>"])
def test_malformed_forwarded_value_falls_back(settings, bad):
    settings.TRUSTED_PROXY_COUNT = 1
    assert client_ip(req(xff=bad)) == "203.0.113.5"


def test_malformed_remote_addr_returns_none(settings):
    settings.TRUSTED_PROXY_COUNT = 0
    assert client_ip(req(remote="garbage")) is None


def test_ipv6_is_supported(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    assert client_ip(req(xff="2001:db8::1")) == "2001:db8::1"

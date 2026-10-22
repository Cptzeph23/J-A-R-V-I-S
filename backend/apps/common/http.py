"""HTTP helpers shared by throttling, audit and authentication."""

from __future__ import annotations

import ipaddress

from django.conf import settings
from django.http import HttpRequest


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def client_ip(request: HttpRequest) -> str | None:
    """Best-effort client address that cannot be spoofed through X-Forwarded-For.

    With TRUSTED_PROXY_COUNT = 0 (default) only REMOTE_ADDR is used. With N > 0 the
    address N entries from the right of X-Forwarded-For is used, because the N trusted
    proxies each append one entry; anything a client put further left is ignored. If the
    header is shorter than the proxy chain or the value is malformed, REMOTE_ADDR wins.
    """
    remote = request.META.get("REMOTE_ADDR")
    remote_ip = remote if isinstance(remote, str) and _valid_ip(remote) else None
    proxies = int(getattr(settings, "TRUSTED_PROXY_COUNT", 0))
    if proxies <= 0:
        return remote_ip
    parts = [p.strip() for p in str(request.META.get("HTTP_X_FORWARDED_FOR", "")).split(",")]
    parts = [p for p in parts if p]
    if len(parts) < proxies:
        return remote_ip
    candidate = parts[-proxies]
    return candidate if _valid_ip(candidate) else remote_ip

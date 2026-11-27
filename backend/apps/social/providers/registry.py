from __future__ import annotations

from collections.abc import Callable

from .base import SocialProvider, SocialProviderError
from .facebook import FacebookPages

_PROVIDERS: dict[str, Callable[[], SocialProvider]] = {"facebook": FacebookPages}


def get_provider(name: str) -> SocialProvider:
    try:
        return _PROVIDERS[name]()
    except KeyError:
        raise SocialProviderError("Unknown provider.") from None

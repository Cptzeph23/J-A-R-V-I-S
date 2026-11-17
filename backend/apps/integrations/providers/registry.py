from __future__ import annotations

from collections.abc import Callable

from .base import CalendarProvider, MailProvider, OAuthProvider, ProviderError
from .google.calendar import GoogleCalendarClient
from .google.gmail import GmailClient
from .google.oauth import GoogleOAuth

_OAUTH: dict[str, Callable[[], OAuthProvider]] = {"google": GoogleOAuth}
_MAIL: dict[str, Callable[[], MailProvider]] = {"google": GmailClient}
_CALENDAR: dict[str, Callable[[], CalendarProvider]] = {"google": GoogleCalendarClient}


def get_oauth_provider(provider: str) -> OAuthProvider:
    try:
        return _OAUTH[provider]()
    except KeyError:
        raise ProviderError("Unknown provider.") from None


def get_mail_provider(provider: str) -> MailProvider:
    try:
        return _MAIL[provider]()
    except KeyError:
        raise ProviderError("Unknown provider.") from None


def get_calendar_provider(provider: str) -> CalendarProvider:
    try:
        return _CALENDAR[provider]()
    except KeyError:
        raise ProviderError("Unknown provider.") from None

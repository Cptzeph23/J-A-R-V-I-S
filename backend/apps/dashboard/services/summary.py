"""Assemble dashboard cards. Every card carries `available`, `reason`, `source` and
`data_as_of` (SRS-DSH-03); an unavailable card has no numbers at all."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apps.accounts.models import User
from apps.common.timezones import iso_utc

from ..ranges import DashboardRange
from ..selectors.activity import activity_card
from ..selectors.calls import calls_card
from ..selectors.email import email_card
from ..selectors.messages import messages_card
from ..selectors.spending import spending_card


def _iso(value: Any) -> Any:
    return iso_utc(value) if isinstance(value, datetime) else value


def with_iso(card: dict[str, Any]) -> dict[str, Any]:
    return {**card, "data_as_of": _iso(card.get("data_as_of"))}


def summary(owner: User, rng: DashboardRange, *, currency: str) -> dict[str, Any]:
    return {
        "range": rng.to_dict(),
        "spending": with_iso(spending_card(owner, rng, currency=currency)),
        "calls": with_iso(calls_card(owner, rng)),
        "messages": with_iso(messages_card(owner, rng)),
        "email": with_iso(email_card(owner)),
        "activity": with_iso(activity_card(owner)),
    }


def card_payload(
    name: str, owner: User, rng: DashboardRange | None, *, currency: str
) -> dict[str, Any]:
    """One card as JSON-ready data. Range-based cards echo the resolved range."""
    if name == "email":
        return with_iso(email_card(owner))
    if name == "activity":
        return with_iso(activity_card(owner))
    if rng is None:
        raise ValueError(f"The {name} card needs a range")
    if name == "spending":
        return {"range": rng.to_dict(), **with_iso(spending_card(owner, rng, currency=currency))}
    if name == "calls":
        return {"range": rng.to_dict(), **with_iso(calls_card(owner, rng))}
    if name == "messages":
        return {"range": rng.to_dict(), **with_iso(messages_card(owner, rng))}
    if name == "summary":
        return summary(owner, rng, currency=currency)
    raise ValueError(f"Unknown card {name!r}")

"""Google Calendar REST v3. Scope: https://www.googleapis.com/auth/calendar.events.

STATUS: written from Google's documented REST shapes; NOT yet exercised against the live
service (this environment cannot reach it). Run one real connect, sync and create before
relying on it.

Privacy: list requests ask only for the fields the product uses (`fields=`), so event
descriptions and attendee addresses are never transferred. Every write passes
`sendUpdates=none`, so no invitation or notification email is ever sent from here.
"""

from __future__ import annotations

import urllib.parse
from datetime import UTC, date, datetime
from typing import Any

from ..base import (
    CalendarEventData,
    EventNotFound,
    EventSpec,
    HistoryExpired,
    ProviderError,
    ProviderUnavailable,
)
from . import http

BASE = "https://www.googleapis.com/calendar/v3/calendars/primary"
SCOPE_CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
PAGE_SIZE = 250
_LIST_FIELDS = (
    "nextPageToken,items(id,etag,status,summary,location,start,end,transparency,"
    "recurringEventId,updated,organizer/self,attendees(self,responseStatus),"
    "hangoutLink,conferenceData/conferenceId)"
)
_ITEM_FIELDS = _LIST_FIELDS.split("items(", 1)[1].rstrip(")")


def _quote(event_id: str) -> str:
    return urllib.parse.quote(event_id, safe="")


def _parse_when(block: Any) -> tuple[datetime | date, str]:
    if not isinstance(block, dict):
        raise ProviderUnavailable("event had no usable time")
    zone = block.get("timeZone")
    zone_name = zone if isinstance(zone, str) else ""
    try:
        if "dateTime" in block:
            moment = datetime.fromisoformat(str(block["dateTime"]))
            if moment.tzinfo is None:
                raise ValueError
            return moment.astimezone(UTC), zone_name
        return date.fromisoformat(str(block["date"])), zone_name
    except (KeyError, ValueError):
        raise ProviderUnavailable("event had no usable time") from None


def _parse_updated(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def parse_event(item: dict[str, Any]) -> CalendarEventData:
    start, start_zone = _parse_when(item.get("start"))
    end, end_zone = _parse_when(item.get("end"))
    attendees = [a for a in item.get("attendees", []) if isinstance(a, dict)]
    mine = next((a for a in attendees if a.get("self")), {})
    organizer = item.get("organizer")
    status = str(item.get("status", "confirmed"))
    return CalendarEventData(
        id=str(item["id"]),
        recurring_event_id=str(item.get("recurringEventId", "")),
        title=str(item.get("summary", "")),
        location=str(item.get("location", "")),
        start=start,
        end=end,
        timezone=start_zone or end_zone,
        status=status if status in {"confirmed", "tentative", "cancelled"} else "confirmed",
        transparent=item.get("transparency") == "transparent",
        self_response=str(mine.get("responseStatus", "")),
        is_organizer=bool(isinstance(organizer, dict) and organizer.get("self")),
        attendee_count=sum(1 for a in attendees if not a.get("self")),
        has_conference=bool(item.get("hangoutLink") or item.get("conferenceData")),
        etag=str(item.get("etag", "")),
        updated_at=_parse_updated(item.get("updated")),
    )


def _when_body(moment: datetime, zone: str) -> dict[str, str]:
    body = {"dateTime": moment.isoformat()}
    if zone:
        body["timeZone"] = zone
    return body


def _write_body(spec: EventSpec) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if spec.title is not None:
        body["summary"] = spec.title
    if spec.location is not None:
        body["location"] = spec.location
    if spec.description is not None:
        body["description"] = spec.description
    zone = spec.timezone or ""
    if spec.start is not None:
        body["start"] = _when_body(spec.start, zone)
    if spec.end is not None:
        body["end"] = _when_body(spec.end, zone)
    return body


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class GoogleCalendarClient:
    def __init__(self, opener: http.Opener | None = None) -> None:
        self._opener = opener

    def _call(
        self,
        method: str,
        url: str,
        token: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return http.call(
            method, url, bearer=token, json_body=body, extra_headers=headers, opener=self._opener
        )

    def account_email(self, access_token: str) -> str:
        body = self._call("GET", f"{BASE}?fields=id", access_token)
        identifier = body.get("id")
        if not isinstance(identifier, str) or "@" not in identifier:
            raise ProviderUnavailable("calendar response was incomplete")
        return identifier.lower()

    def list_events(
        self, access_token: str, *, time_min: datetime, time_max: datetime, page_token: str = ""
    ) -> tuple[list[CalendarEventData], str]:
        params = [
            ("singleEvents", "true"),
            ("orderBy", "startTime"),
            ("showDeleted", "false"),
            ("maxResults", str(PAGE_SIZE)),
            ("timeMin", _utc(time_min)),
            ("timeMax", _utc(time_max)),
            ("fields", _LIST_FIELDS),
        ]
        if page_token:
            params.append(("pageToken", page_token))
        body = self._call("GET", f"{BASE}/events?{urllib.parse.urlencode(params)}", access_token)
        items = [i for i in body.get("items", []) if isinstance(i, dict) and "id" in i]
        return [parse_event(i) for i in items], str(body.get("nextPageToken", ""))

    def get_event(self, access_token: str, event_id: str) -> CalendarEventData | None:
        url = f"{BASE}/events/{_quote(event_id)}?{urllib.parse.urlencode({'fields': _ITEM_FIELDS})}"
        try:
            item = self._call("GET", url, access_token)
        except HistoryExpired:  # 404 or 410
            return None
        if not item.get("id"):
            raise ProviderUnavailable("event response was incomplete")
        event = parse_event(item)
        return None if event.status == "cancelled" else event

    def insert_event(self, access_token: str, event_id: str, spec: EventSpec) -> CalendarEventData:
        if spec.start is None or spec.end is None:
            raise ProviderError("An event needs a start and an end.")
        query = urllib.parse.urlencode({"sendUpdates": "none", "fields": _ITEM_FIELDS})
        item = self._call(
            "POST",
            f"{BASE}/events?{query}",
            access_token,
            body={"id": event_id, **_write_body(spec)},
        )
        return parse_event(item)

    def patch_event(
        self, access_token: str, event_id: str, spec: EventSpec, *, etag: str
    ) -> CalendarEventData:
        query = urllib.parse.urlencode({"sendUpdates": "none", "fields": _ITEM_FIELDS})
        try:
            item = self._call(
                "PATCH",
                f"{BASE}/events/{_quote(event_id)}?{query}",
                access_token,
                body=_write_body(spec),
                headers={"If-Match": etag} if etag else None,
            )
        except HistoryExpired:
            raise EventNotFound("no such event") from None
        return parse_event(item)

    def delete_event(self, access_token: str, event_id: str, *, etag: str) -> None:
        query = urllib.parse.urlencode({"sendUpdates": "none"})
        try:
            self._call(
                "DELETE",
                f"{BASE}/events/{_quote(event_id)}?{query}",
                access_token,
                headers={"If-Match": etag} if etag else None,
            )
        except HistoryExpired:
            raise EventNotFound("no such event") from None

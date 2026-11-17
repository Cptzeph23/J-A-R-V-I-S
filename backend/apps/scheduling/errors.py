"""Failures the assistant's calendar writes can report. Codes are stable; messages are safe to
show to the owner and to the model (no titles, no provider text)."""

from __future__ import annotations


class CalendarError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def unavailable() -> CalendarError:
    return CalendarError(
        "calendar_unavailable", "The calendar is not connected or needs to be reconnected."
    )

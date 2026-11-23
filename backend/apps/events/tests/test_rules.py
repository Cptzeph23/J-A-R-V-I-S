from __future__ import annotations

import pytest

from apps.events.rules import assess, category_of, render


@pytest.mark.parametrize(
    ("payload", "importance", "notify", "rule"),
    [
        ({"important": True, "needs_reply": True}, "high", True, "mail_important_and_needs_reply"),
        ({"important": True, "needs_reply": False}, "normal", True, "mail_important"),
        ({"important": False, "needs_reply": True}, "normal", True, "mail_needs_reply"),
        ({"important": False, "needs_reply": False}, "low", False, "mail_other"),
        ({}, "low", False, "mail_other"),
    ],
)
def test_mail_rules_are_explainable(payload, importance, notify, rule):
    verdict = assess("mail.important", payload)
    assert (verdict.importance, verdict.notify, verdict.reason) == (importance, notify, rule)
    assert verdict.category == "mail"


def test_a_truthy_string_is_not_a_flag():
    assert assess("mail.important", {"important": "yes"}).notify is False


@pytest.mark.parametrize(
    ("event_type", "importance", "category"),
    [
        ("calendar.starting_soon", "high", "calendar"),
        ("calendar.conflict", "normal", "calendar"),
        ("integration.reauth_required", "high", "system"),
        ("approval.pending", "high", "approvals"),
    ],
)
def test_other_event_types(event_type, importance, category):
    verdict = assess(event_type, {})
    assert (verdict.importance, verdict.notify, verdict.category) == (importance, True, category)


def test_push_signals_and_unknown_types_never_notify():
    assert assess("mail.push", {}).notify is False
    unknown = assess("made.up", {"important": True})
    assert (unknown.notify, unknown.reason) == (False, "unknown_type")


def test_unknown_prefix_falls_into_system():
    assert category_of("whatever.thing") == "system"


def test_text_is_generic_and_counts_group_members():
    one = render("mail.important", 1, {"subject": "Salary details"})
    many = render("mail.important", 4, {})
    assert one.title == "Important email"
    assert "Salary" not in one.title + one.body + str(one.data)
    assert many.title == "4 important emails"


def test_calendar_text_says_when_but_never_what():
    soon = render("calendar.starting_soon", 1, {"minutes": 10, "title": "Therapy"})
    assert "in 10 minutes" in soon.body
    assert "Therapy" not in soon.title + soon.body

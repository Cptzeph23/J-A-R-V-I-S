"""Activity card: counts of what needs attention, per owner, never text."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import Approval
from apps.notifications.models import Notification
from tests.agent_fixtures import pending_approval, start
from tests.auth_fixtures import AuthedClient

URL = "/api/v1/dashboard/activity"
pytestmark = pytest.mark.django_db


def notify(owner: User, priority: str, *, state: str = "dispatched", read: bool = False) -> None:
    Notification.objects.create(
        owner=owner,
        category="system",
        priority=priority,
        title="t",
        body="b",
        group_key=f"g-{priority}-{state}-{read}-{Notification.objects.count()}",
        state=state,
        read_at=timezone.now() if read else None,
    )


def test_empty_activity_is_available_and_zero(client_a: AuthedClient):
    body = client_a.get(URL).json()
    assert body["available"] is True
    assert body["reason"] is None
    assert body["pending_approvals"] == 0
    assert body["active_workflows"] == 0
    assert body["unread_notifications"] == {
        "total": 0,
        "by_priority": {"low": 0, "normal": 0, "high": 0, "critical": 0},
    }
    assert body["data_as_of"].endswith("Z")


def test_counts_pending_approvals_unread_notifications_and_running_tasks(
    owner_a: User, client_a: AuthedClient
):
    pending_approval(owner_a)
    start(owner_a)
    notify(owner_a, "high")
    notify(owner_a, "high")
    notify(owner_a, "low")
    notify(owner_a, "normal", read=True)  # already read
    notify(owner_a, "critical", state="held")  # not delivered yet
    body = client_a.get(URL).json()
    assert body["pending_approvals"] == 1
    assert body["active_workflows"] >= 1
    assert body["unread_notifications"]["by_priority"] == {
        "low": 1,
        "normal": 0,
        "high": 2,
        "critical": 0,
    }
    assert body["unread_notifications"]["total"] == 3


def test_expired_approvals_are_not_counted(owner_a: User, client_a: AuthedClient):
    approval = pending_approval(owner_a)
    Approval.objects.filter(pk=approval.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    assert client_a.get(URL).json()["pending_approvals"] == 0


def test_card_reflects_a_new_notification_immediately(owner_a: User, client_a: AuthedClient):
    assert client_a.get(URL).json()["unread_notifications"]["total"] == 0
    notify(owner_a, "normal")
    assert client_a.get(URL).json()["unread_notifications"]["total"] == 1  # cache invalidated


def test_other_owners_activity_is_not_counted(owner_a: User, owner_b: User, client_b: AuthedClient):
    pending_approval(owner_a)
    notify(owner_a, "critical")
    body = client_b.get(URL).json()
    assert body["pending_approvals"] == 0
    assert body["unread_notifications"]["total"] == 0

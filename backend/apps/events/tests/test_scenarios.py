"""Scenarios 1, 5, 13 and 18 through the real pipeline (eager tasks, fake Google, fake push)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.events.models import Event, EventStatus
from apps.events.tasks import process_event_task, reconcile_task
from apps.integrations.models import Integration, IntegrationStatus
from apps.integrations.providers.base import GrantRevoked
from apps.mailbox.sync import run_sync
from apps.notifications.models import (
    DeliveryStatus,
    Notification,
    NotificationDelivery,
    NotificationState,
)
from apps.notifications.providers.base import ProviderUnavailable
from apps.notifications.services import delivery, planning
from apps.notifications.tasks import recover_task
from tests.factories import UserFactory
from tests.mail_fixtures import make_connected
from tests.notification_fixtures import register_device, retry_time

pytestmark = pytest.mark.django_db


@pytest.fixture
def phone(fake_google, fake_push):
    owner = UserFactory()
    integration = make_connected(owner, fake_google)  # first sync done: history is stored
    device = register_device(owner)
    return owner, integration, device


def arrive(fake_google, message_id="m-new", **kw):
    fake_google.add_message(
        message_id,
        received=timezone.now(),
        labels=kw.pop("labels", ("INBOX", "UNREAD", "IMPORTANT")),
        **kw,
    )


def test_scenario_1_important_mail_while_the_app_is_closed_sends_a_push_and_records_it(
    phone, fake_google, fake_push, django_capture_on_commit_callbacks
):
    _, integration, device = phone
    arrive(fake_google, subject="Salary breakdown", sender="boss@example.org")
    with django_capture_on_commit_callbacks(execute=True):
        assert run_sync(integration.pk).outcome == "synced"

    assert len(fake_push.sent) == 1
    _, message = fake_push.sent[0]
    assert message.title == "Important email"
    assert "Salary" not in message.title + message.body + str(message.data)  # no content leaks
    row = NotificationDelivery.objects.get()
    assert (row.status, row.attempts, row.device_id) == ("sent", 1, device.pk)
    assert row.sent_at is not None
    note = Notification.objects.get()
    assert (note.state, note.category) == ("dispatched", "mail")
    event = Event.objects.get()
    assert (event.status, event.notification_id) == ("processed", note.pk)


def test_a_backlog_on_the_first_sync_does_not_become_a_burst_of_pushes(
    fake_google, fake_push, django_capture_on_commit_callbacks
):
    owner = UserFactory()
    for i in range(30):
        arrive(fake_google, f"old-{i}")
    register_device(owner)
    with django_capture_on_commit_callbacks(execute=True):
        make_connected(owner, fake_google)
    assert fake_push.sent == []
    assert Event.objects.count() == 0


def test_mail_that_is_neither_important_nor_needs_a_reply_stays_quiet(
    phone, fake_google, fake_push, django_capture_on_commit_callbacks
):
    _, integration, _ = phone
    arrive(
        fake_google,
        labels=("INBOX", "UNREAD", "CATEGORY_PROMOTIONS"),
        bulk=frozenset({"list-unsubscribe"}),
    )
    with django_capture_on_commit_callbacks(execute=True):
        run_sync(integration.pk)
    assert fake_push.sent == []


def test_syncing_the_same_message_again_does_not_notify_again(
    phone, fake_google, fake_push, django_capture_on_commit_callbacks
):
    _, integration, _ = phone
    arrive(fake_google)
    with django_capture_on_commit_callbacks(execute=True):
        run_sync(integration.pk)
        fake_google.set_labels("m-new", ("INBOX", "UNREAD", "IMPORTANT", "STARRED"))
        run_sync(integration.pk)
    assert len(fake_push.sent) == 1
    assert Event.objects.count() == 1


def test_scenario_13_the_provider_is_down_then_recovers_without_a_duplicate(
    phone, fake_google, fake_push, django_capture_on_commit_callbacks
):
    _, integration, _ = phone
    fake_push.fail_next(ProviderUnavailable("down"), ProviderUnavailable("down"))
    arrive(fake_google)
    with django_capture_on_commit_callbacks(execute=True):
        run_sync(integration.pk)
    row = NotificationDelivery.objects.get()
    assert (row.status, row.attempts) == (DeliveryStatus.FAILED_RETRY, 1)  # status is visible
    assert fake_push.sent == []

    when = retry_time(row) + timedelta(seconds=1)
    delivery.send_delivery(row.pk, when)  # still down
    row.refresh_from_db()
    delivery.send_delivery(row.pk, retry_time(row) + timedelta(seconds=1))  # recovered
    row.refresh_from_db()
    assert (row.status, row.attempts) == (DeliveryStatus.SENT, 3)
    assert len(fake_push.sent) == 1


def test_scenario_5_a_crash_while_processing_loses_nothing_and_duplicates_nothing(
    phone, monkeypatch, django_capture_on_commit_callbacks
):
    owner, _, _ = phone
    event = Event.objects.create(
        owner=owner, source="system", external_event_id="a-1", type="approval.pending", payload={}
    )
    real = planning.create_for_event
    calls = {"n": 0}

    def crash_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("worker killed")
        return real(*args, **kwargs)

    monkeypatch.setattr(planning, "create_for_event", crash_once)
    with pytest.raises(RuntimeError):
        process_event_task(str(event.pk))
    event.refresh_from_db()
    assert event.status == EventStatus.NEW  # the failed attempt left no trace
    assert Notification.objects.count() == 0

    with django_capture_on_commit_callbacks(execute=True):
        process_event_task(str(event.pk))  # redelivery
        process_event_task(str(event.pk))  # and a second, spurious one
    assert Notification.objects.count() == 1
    assert NotificationDelivery.objects.count() == 1


def test_scenario_5_a_crash_between_fan_out_and_send_resumes_the_send(
    phone, fake_push, django_capture_on_commit_callbacks
):
    owner, _, _ = phone
    note = Notification.objects.create(
        owner=owner,
        category="system",
        priority="high",
        title="Approval needed",
        body="Waiting.",
        group_key="approval.pending",
        state=NotificationState.READY,
    )
    delivery.fan_out(note.pk)  # the worker dies right after this
    assert fake_push.sent == []
    later = timezone.now() + timedelta(minutes=5)
    assert len(delivery.due_delivery_ids(later)) == 1
    delivery.send_delivery(delivery.due_delivery_ids(later)[0], later)
    delivery.send_delivery(NotificationDelivery.objects.get().pk, later)  # duplicate message
    assert len(fake_push.sent) == 1


def test_scenario_18_a_restart_with_jobs_pending_resumes_everything_once(
    phone, fake_push, django_capture_on_commit_callbacks
):
    owner, _, device = phone
    now = timezone.now()
    # 1. an event nobody processed (the broker lost the message)
    stuck_event = Event.objects.create(
        owner=owner, source="system", external_event_id="e-1", type="approval.pending", payload={}
    )
    Event.objects.filter(pk=stuck_event.pk).update(created_at=now - timedelta(minutes=5))
    # 2. a notification that was ready but never fanned out
    ready = Notification.objects.create(
        owner=owner,
        category="system",
        priority="high",
        title="Reconnect an account",
        body="x",
        group_key="integration.reauth_required",
        state=NotificationState.READY,
    )
    Notification.objects.filter(pk=ready.pk).update(created_at=now - timedelta(minutes=5))
    # 3. a held notification whose quiet hours ended while the system was down
    held = Notification.objects.create(
        owner=owner,
        category="calendar",
        priority="normal",
        title="Calendar conflict",
        body="x",
        group_key="calendar.conflict",
        state=NotificationState.HELD,
        scheduled_for=now - timedelta(minutes=1),
    )
    # 4. a delivery that was waiting for its retry time
    waiting = Notification.objects.create(
        owner=owner,
        category="mail",
        priority="high",
        title="Important email",
        body="x",
        group_key="mail.important",
        state=NotificationState.DISPATCHED,
        dispatched_at=now,
    )
    retry = NotificationDelivery.objects.create(
        owner=owner,
        notification=waiting,
        device=device,
        provider="fcm",
        status=DeliveryStatus.FAILED_RETRY,
        attempts=1,
        next_attempt_at=now - timedelta(seconds=5),
    )

    with django_capture_on_commit_callbacks(execute=True):
        reconcile_task()
        recover_task()

    stuck_event.refresh_from_db()
    assert stuck_event.status == EventStatus.PROCESSED
    for pk in (ready.pk, held.pk, waiting.pk):
        assert Notification.objects.get(pk=pk).state == NotificationState.DISPATCHED
    retry.refresh_from_db()
    assert retry.status == DeliveryStatus.SENT
    sent_after_first = len(fake_push.sent)
    deliveries_after_first = NotificationDelivery.objects.count()
    assert sent_after_first == 4  # one push per notification, none doubled

    with django_capture_on_commit_callbacks(execute=True):
        reconcile_task()
        recover_task()
        recover_task()
    assert len(fake_push.sent) == sent_after_first  # a second pass changes nothing
    assert NotificationDelivery.objects.count() == deliveries_after_first


def test_a_lost_grant_tells_the_owner_once_a_day(
    phone, fake_google, fake_push, django_capture_on_commit_callbacks
):
    _owner, integration, _ = phone
    fake_google.fail_next(GrantRevoked("revoked"))
    with django_capture_on_commit_callbacks(execute=True):
        run_sync(integration.pk)
    Integration.objects.filter(pk=integration.pk).update(
        status=IntegrationStatus.CONNECTED, sync_lease_until=None
    )
    fake_google.fail_next(GrantRevoked("revoked"))
    with django_capture_on_commit_callbacks(execute=True):
        run_sync(integration.pk)
    assert [m.title for _, m in fake_push.sent] == ["Reconnect an account"]


def test_an_approval_request_pings_the_owner(phone, fake_push, django_capture_on_commit_callbacks):
    from tests.agent_fixtures import pending_approval

    owner, _, _ = phone
    with django_capture_on_commit_callbacks(execute=True):
        approval = pending_approval(owner)
    assert Event.objects.filter(
        type="approval.pending", external_event_id=f"approval:{approval.pk}"
    )
    assert [m.title for _, m in fake_push.sent] == ["Approval needed"]

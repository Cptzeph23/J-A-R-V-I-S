from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.dashboard.selectors.email import email_card
from apps.integrations.models import Integration, IntegrationStatus
from tests.factories import UserFactory
from tests.mail_fixtures import make_connected

pytestmark = pytest.mark.django_db


def test_not_connected_is_explicit():
    card = email_card(UserFactory())
    assert card["available"] is False
    assert card["reason"] == "mail_not_connected"


def test_connected_but_never_synced_does_not_show_zeros(fake_google):
    owner = UserFactory()
    make_connected(owner, fake_google, sync=False)
    assert email_card(owner)["reason"] == "first_sync_pending"


def test_counts_come_from_synced_inbox_messages(fake_google):
    owner = UserFactory()
    fake_google.add_message("a", labels=("INBOX", "UNREAD", "IMPORTANT"))
    fake_google.add_message("b", labels=("INBOX", "UNREAD", "CATEGORY_PROMOTIONS"))
    fake_google.add_message("c", labels=("INBOX",))
    fake_google.add_message("d", labels=("SENT",))
    make_connected(owner, fake_google)
    card = email_card(owner)
    assert card["available"] is True
    assert card["source"] == "gmail"
    assert (card["unread"], card["important_unread"], card["needs_reply"]) == (2, 1, 2)
    assert card["degraded"] is False
    assert card["data_as_of"] is not None


def test_degraded_and_reconnect_states_are_visible(fake_google):
    owner = UserFactory()
    integration = make_connected(owner, fake_google)
    Integration.objects.filter(pk=integration.pk).update(status=IntegrationStatus.DEGRADED)
    assert email_card(owner)["degraded"] is True
    Integration.objects.filter(pk=integration.pk).update(status=IntegrationStatus.REAUTH_REQUIRED)
    assert email_card(owner)["reason"] == "reconnect_needed"
    Integration.objects.filter(pk=integration.pk).update(status=IntegrationStatus.REVOKED)
    assert email_card(owner)["reason"] == "mail_not_connected"


def test_other_owners_mail_is_never_counted(fake_google):
    a, b = UserFactory(), UserFactory()
    fake_google.add_message("a")
    make_connected(a, fake_google)
    fake_google.email = "b@example.com"
    make_connected(b, fake_google, sync=False)
    Integration.objects.filter(owner=b).update(last_synced_at=timezone.now() - timedelta(minutes=1))
    assert email_card(b)["unread"] == 0

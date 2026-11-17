from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditEntry
from apps.common import secretbox
from apps.integrations.models import Integration, IntegrationStatus, OAuthCredential
from apps.integrations.services import credentials
from apps.integrations.services.errors import IntegrationUnusableError
from apps.mailbox.models import MailMessage
from apps.mailbox.sync import run_sync
from tests.factories import UserFactory
from tests.mail_fixtures import connect

pytestmark = pytest.mark.django_db


def _expire(integration: Integration) -> None:
    OAuthCredential.objects.filter(integration=integration).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )


def test_a_fresh_token_is_returned_without_calling_the_provider(fake_google):
    integration = connect(UserFactory())
    assert credentials.access_token_for(integration).startswith("fake-access-")
    assert fake_google.refreshes == 0


def test_an_expiring_token_is_refreshed_once_and_the_refresh_token_kept(fake_google):
    integration = connect(UserFactory())
    _expire(integration)
    first = credentials.access_token_for(integration)
    second = credentials.access_token_for(integration)
    assert first == second
    assert fake_google.refreshes == 1
    cred = OAuthCredential.objects.get()
    assert (
        secretbox.open_sealed(cred.refresh_token_sealed, f"oauth.refresh:{cred.pk}")
        == fake_google.REFRESH
    )


def test_a_lost_grant_marks_reconnect_needed_and_deletes_tokens(fake_google):
    owner = UserFactory()
    integration = connect(owner)
    _expire(integration)
    fake_google.grant_ok = False
    with pytest.raises(IntegrationUnusableError):
        credentials.access_token_for(integration)
    integration.refresh_from_db()
    assert integration.status == IntegrationStatus.REAUTH_REQUIRED
    assert not OAuthCredential.objects.exists()
    assert AuditEntry.objects.filter(action="integration.grant_lost", owner_id=owner.pk).exists()
    with pytest.raises(IntegrationUnusableError):
        credentials.access_token_for(integration)


def test_scenario_10_revoking_access_deletes_tokens_stops_sync_and_is_audited(fake_google):
    owner = UserFactory()
    fake_google.add_message("m1")
    integration = connect(owner)
    run_sync(integration.pk)
    assert MailMessage.objects.count() == 1

    result = credentials.disconnect(owner, integration.pk)

    assert result.status == IntegrationStatus.REVOKED
    assert fake_google.revoked == [fake_google.REFRESH]
    assert not OAuthCredential.objects.exists()
    assert not MailMessage.objects.exists()
    calls_before = len(fake_google.calls)
    assert run_sync(integration.pk).outcome == "busy"  # an inactive integration is never claimed
    assert len(fake_google.calls) == calls_before
    entry = AuditEntry.objects.get(action="integration.disconnected")
    assert entry.detail["revoked_at_provider"] is True


def test_tokens_are_deleted_even_when_the_provider_cannot_revoke(fake_google):
    owner = UserFactory()
    integration = connect(owner)
    fake_google.revoke_fails = True
    credentials.disconnect(owner, integration.pk)
    assert not OAuthCredential.objects.exists()
    assert (
        AuditEntry.objects.get(action="integration.disconnected").detail["revoked_at_provider"]
        is False
    )


def test_disconnect_twice_is_harmless_and_foreign_ids_are_hidden(fake_google):
    from rest_framework.exceptions import NotFound

    owner = UserFactory()
    integration = connect(owner)
    credentials.disconnect(owner, integration.pk)
    credentials.disconnect(owner, integration.pk)
    assert AuditEntry.objects.filter(action="integration.disconnected").count() == 1
    with pytest.raises(NotFound):
        credentials.disconnect(UserFactory(), integration.pk)

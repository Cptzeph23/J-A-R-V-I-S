"""Start a sync for whichever kind of integration this is (imports are local: the sync apps
depend on this one, not the other way round)."""

from __future__ import annotations

from ..models import Integration, Kind


def enqueue_sync_for(integration: Integration) -> None:
    if integration.kind == Kind.CALENDAR:
        from apps.scheduling.tasks import enqueue_sync as enqueue_calendar

        enqueue_calendar(integration.pk)
    else:
        from apps.mailbox.tasks import enqueue_sync as enqueue_mail

        enqueue_mail(integration.pk)

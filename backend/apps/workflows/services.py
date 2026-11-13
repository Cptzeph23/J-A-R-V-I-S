"""Workflow lifecycle: creation, guarded transitions, leases and usage counters.

Every state change goes through `transition`, which locks the row, checks the table in
`apps.agent.state_machine`, and refuses anything else (SRS-AGT-06). A refused transition is
audited outside the failing transaction so the record survives.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.state_machine import TERMINAL, InvalidTransition, WorkflowState, check_transition
from apps.audit.models import AuditActor
from apps.audit.recorder import record
from apps.conversations.models import Conversation, Message

from .errors import WorkflowActiveError
from .models import Workflow

_TERMINAL_VALUES = [s.value for s in TERMINAL]
_AUDITED = TERMINAL | {WorkflowState.AWAITING_APPROVAL, WorkflowState.NEEDS_REVIEW}


def create_workflow(
    *, owner: User, conversation: Conversation, trigger_message: Message, origin: str
) -> Workflow:
    try:
        with transaction.atomic():
            workflow = Workflow.objects.create(
                owner=owner,
                conversation=conversation,
                trigger_message=trigger_message,
                goal=trigger_message.content[:500],
                origin=origin,
            )
            record(
                "agent.workflow_created",
                owner_id=owner.pk,
                actor=AuditActor.AGENT,
                target_type="workflow",
                target_id=workflow.pk,
                detail={"origin": origin},
            )
    except IntegrityError as exc:
        raise WorkflowActiveError() from exc
    return workflow


def transition(
    workflow_id: uuid.UUID,
    to: WorkflowState,
    *,
    error_code: str = "",
    error_message: str = "",
    answer_message: Message | None = None,
    now: datetime | None = None,
) -> Workflow:
    """Move a workflow to `to` or raise `InvalidTransition` (audited, nothing changed)."""
    moment = now or timezone.now()
    refused: tuple[uuid.UUID, str] | None = None
    try:
        with transaction.atomic():
            workflow = Workflow.objects.select_for_update().get(pk=workflow_id)
            try:
                check_transition(workflow.state, to.value)
            except InvalidTransition:
                refused = (workflow.owner_id, workflow.state)
                raise
            previous = workflow.state
            workflow.state = to.value
            if to is WorkflowState.PLANNING and workflow.started_at is None:
                workflow.started_at = moment
            if to in TERMINAL:
                workflow.finished_at = moment
                workflow.lease_token = None
                workflow.lease_expires_at = None
            if error_code:
                workflow.error_code = error_code[:64]
                workflow.error_message = error_message[:300]
            if answer_message is not None:
                workflow.answer_message = answer_message
            workflow.save()
            if to in _AUDITED:
                record(
                    f"agent.workflow_{to.value}",
                    owner_id=workflow.owner_id,
                    actor=AuditActor.AGENT,
                    target_type="workflow",
                    target_id=workflow.pk,
                    detail={"from": previous, "error_code": error_code},
                )
            return workflow
    except InvalidTransition as exc:
        if refused is not None:
            record(
                "agent.invalid_transition",
                owner_id=refused[0],
                actor=AuditActor.SYSTEM,
                target_type="workflow",
                target_id=workflow_id,
                detail={"from": refused[1], "to": to.value},
            )
        raise exc


# -- leases ----------------------------------------------------------------------------


def acquire_lease(workflow_id: uuid.UUID, *, now: datetime) -> uuid.UUID | None:
    """Take the exclusive right to drive a workflow, or None if someone else holds it."""
    token = uuid.uuid4()
    taken = (
        Workflow.objects.filter(pk=workflow_id)
        .exclude(state__in=_TERMINAL_VALUES)
        .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lt=now))
        .update(
            lease_token=token,
            lease_expires_at=now + timedelta(seconds=settings.AGENT_LEASE_SECONDS),
        )
    )
    return token if taken else None


def renew_lease(workflow_id: uuid.UUID, token: uuid.UUID, *, now: datetime) -> bool:
    return bool(
        Workflow.objects.filter(pk=workflow_id, lease_token=token).update(
            lease_expires_at=now + timedelta(seconds=settings.AGENT_LEASE_SECONDS)
        )
    )


def release_lease(workflow_id: uuid.UUID, token: uuid.UUID) -> None:
    Workflow.objects.filter(pk=workflow_id, lease_token=token).update(
        lease_token=None, lease_expires_at=None
    )


# -- counters --------------------------------------------------------------------------


def add_usage(
    workflow_id: uuid.UUID,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    turns: int = 0,
    repairs: int = 0,
) -> None:
    Workflow.objects.filter(pk=workflow_id).update(
        tokens_in=F("tokens_in") + tokens_in,
        tokens_out=F("tokens_out") + tokens_out,
        llm_turns=F("llm_turns") + turns,
        repairs_used=F("repairs_used") + repairs,
    )


def mark_tainted(workflow_id: uuid.UUID) -> None:
    Workflow.objects.filter(pk=workflow_id, tainted=False).update(tainted=True)

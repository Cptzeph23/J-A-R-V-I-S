"""Operations the API and workers share: start a run, decide an approval, cancel, enqueue."""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.models import User
from apps.agent.errors import BudgetExceededError
from apps.agent.state_machine import TERMINAL, WorkflowState
from apps.approvals import services as approval_services
from apps.approvals.errors import ApprovalExpiredError
from apps.approvals.models import Approval
from apps.audit.models import AuditActor
from apps.audit.recorder import record
from apps.common.errors import ConflictError
from apps.conversations.models import (
    Conversation,
    Message,
    MessageRole,
    MessageSource,
    MessageStatus,
)
from apps.devices.models import Device
from apps.workflows import services as wf_services
from apps.workflows.models import StepState, Workflow, WorkflowOrigin

_IDLE = {
    WorkflowState.CREATED,
    WorkflowState.AWAITING_APPROVAL,
    WorkflowState.AWAITING_CLARIFICATION,
    WorkflowState.NEEDS_REVIEW,
}


def enqueue_run(workflow_id: uuid.UUID) -> None:
    """Queue a worker to drive the workflow, once the current transaction commits."""
    from ..tasks import run_workflow_task

    transaction.on_commit(lambda: run_workflow_task.delay(str(workflow_id)))


def tokens_used_since(owner: User, since: object) -> int:
    totals = (
        Workflow.objects.for_owner(owner)
        .filter(created_at__gte=since)
        .aggregate(a=Sum("tokens_in"), b=Sum("tokens_out"))
    )
    return int(totals["a"] or 0) + int(totals["b"] or 0)


def start_run(
    *, owner: User, conversation: Conversation, content: str, source: str
) -> tuple[Message, Workflow]:
    text = content.strip()
    if not text:
        raise ValidationError({"content": ["Must not be empty."]})
    if len(text) > settings.AGENT_MAX_USER_MESSAGE_CHARS:
        raise ValidationError(
            {"content": [f"At most {settings.AGENT_MAX_USER_MESSAGE_CHARS} characters."]}
        )
    if source not in MessageSource.values:
        raise ValidationError({"source": ["Use 'text' or 'voice'."]})
    if conversation.archived_at is not None:
        raise ConflictError("This conversation is archived.")
    now = timezone.now()
    if tokens_used_since(owner, now - timedelta(hours=24)) >= settings.AGENT_DAILY_TOKEN_CAP:
        raise BudgetExceededError()

    with transaction.atomic():
        message = Message.objects.create(
            owner=owner,
            conversation=conversation,
            role=MessageRole.USER,
            content=text,
            source=source,
        )
        workflow = wf_services.create_workflow(
            owner=owner,
            conversation=conversation,
            trigger_message=message,
            origin=WorkflowOrigin.VOICE if source == MessageSource.VOICE else WorkflowOrigin.TEXT,
        )
        Conversation.objects.filter(pk=conversation.pk).update(updated_at=now)
        enqueue_run(workflow.pk)
    return message, workflow


def finalize_cancel(workflow_id: uuid.UUID, *, note: str = "Cancelled.") -> Workflow:
    """Stop a workflow for good: nothing further runs and no approval stays usable."""
    now = timezone.now()
    with transaction.atomic():
        workflow = Workflow.objects.select_for_update().get(pk=workflow_id)
        if WorkflowState(workflow.state) in TERMINAL:
            return workflow
        workflow.steps.filter(state__in=[StepState.PENDING, StepState.AWAITING_APPROVAL]).update(
            state=StepState.CANCELLED,
            finished_at=now,
            is_error=True,
            result_for_model="Cancelled by the owner. Nothing was changed.",
        )
        approval_services.cancel_for_workflow(workflow.pk)
        Message.objects.create(
            owner_id=workflow.owner_id,
            conversation_id=workflow.conversation_id,
            role=MessageRole.SYSTEM_NOTE,
            content=note,
            status=MessageStatus.COMPLETE,
        )
        return wf_services.transition(workflow.pk, WorkflowState.CANCELLED, now=now)


def request_cancel(*, owner: User, workflow_id: uuid.UUID) -> Workflow:
    """Idempotent. An idle workflow stops now; a working one stops after its current call,
    whose outcome is still recorded (SRS-AGT-07)."""
    with transaction.atomic():
        workflow = (
            Workflow.objects.for_owner(owner).select_for_update().filter(pk=workflow_id).first()
        )
        if workflow is None:
            raise NotFound()
        if WorkflowState(workflow.state) in TERMINAL:
            return workflow
        if workflow.cancel_requested_at is None:
            workflow.cancel_requested_at = timezone.now()
            workflow.save(update_fields=["cancel_requested_at", "updated_at"])
            record(
                "agent.workflow_cancel_requested",
                owner_id=owner.pk,
                actor=AuditActor.USER,
                target_type="workflow",
                target_id=workflow.pk,
            )
        if WorkflowState(workflow.state) in _IDLE:
            return finalize_cancel(workflow.pk)
        return workflow


def decide_approval(
    *,
    owner: User,
    approval_id: uuid.UUID,
    decision: str,
    echoed_sha256: str,
    device: Device,
    factor: str,
) -> Approval:
    """Record the owner's decision, then wake the workflow so it can act on it."""
    try:
        # Not wrapped in an outer transaction: an expiry recorded by `decide` must survive
        # the error it raises.
        approval = approval_services.decide(
            owner=owner,
            approval_id=approval_id,
            decision=decision,
            echoed_sha256=echoed_sha256,
            device=device,
            factor=factor,
            now=timezone.now(),
        )
    except ApprovalExpiredError:
        # The expiry was recorded; the workflow still has to be told.
        enqueue_run(Approval.objects.get(pk=approval_id).workflow_id)
        raise
    enqueue_run(approval.workflow_id)
    return approval

"""Approvals: exact-action permission that is hash-bound, short-lived and single-use.

SRS-AGT-03: the payload and its SHA-256 are stored at creation, the client echoes the hash
when deciding, and execution recomputes it from the stored payload AND from the step the
orchestrator is about to run. SRS-AGT-04: approvals expire (default 15 minutes) and can be
consumed once. An approval that is approved but not consumed before `expires_at` also
expires, so a stalled queue cannot resurrect an old decision.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, cast

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.models import User
from apps.audit.models import AuditActor
from apps.audit.recorder import record
from apps.devices.models import Device
from apps.workflows.models import Workflow, WorkflowStep

from .errors import (
    ApprovalExpiredError,
    ApprovalIntegrityError,
    ApprovalNotApprovedError,
    ApprovalNotPendingError,
)
from .hashing import build_action_payload, payload_sha256
from .models import Approval, ApprovalState

APPROVE = "approve"
REJECT = "reject"
FACTOR_TAP = "tap"
FACTOR_VOICE = "voice"
_LIVE = [ApprovalState.PENDING, ApprovalState.APPROVED]


def create_for_step(
    *,
    workflow: Workflow,
    step: WorkflowStep,
    args: Mapping[str, Any],
    summary: str,
    tainted: bool,
    now: datetime,
) -> Approval:
    payload = build_action_payload(
        owner_id=workflow.owner_id,
        workflow_id=workflow.pk,
        step_seq=step.seq,
        tool_name=step.tool_name,
        args=args,
    )
    approval = Approval.objects.create(
        owner_id=workflow.owner_id,
        workflow=workflow,
        step=step,
        tool_name=step.tool_name,
        action_payload=payload,
        payload_sha256=payload_sha256(payload),
        destination_summary=summary[:300],
        tainted=tainted,
        expires_at=now + timedelta(seconds=settings.AGENT_APPROVAL_TTL_SECONDS),
    )
    record(
        "approval.requested",
        owner_id=workflow.owner_id,
        actor=AuditActor.AGENT,
        target_type="approval",
        target_id=approval.pk,
        detail={"tool": step.tool_name, "tainted": tainted},
    )
    return approval


def _expire(approval: Approval, now: datetime) -> None:
    approval.state = ApprovalState.EXPIRED
    approval.save(update_fields=["state", "updated_at"])
    record(
        "approval.expired",
        owner_id=approval.owner_id,
        actor=AuditActor.SYSTEM,
        target_type="approval",
        target_id=approval.pk,
        detail={"tool": approval.tool_name},
    )


def decide(
    *,
    owner: User,
    approval_id: uuid.UUID,
    decision: str,
    echoed_sha256: str,
    device: Device,
    factor: str,
    now: datetime,
) -> Approval:
    if decision not in {APPROVE, REJECT}:
        raise ValidationError({"decision": ["Use 'approve' or 'reject'."]})
    if factor not in {FACTOR_TAP, FACTOR_VOICE}:
        raise ValidationError({"confirmation": ["Unknown confirmation factor."]})
    if decision == APPROVE and factor != FACTOR_TAP:
        raise ValidationError({"confirmation": ["Approving requires a tap confirmation."]})

    wanted = ApprovalState.APPROVED if decision == APPROVE else ApprovalState.REJECTED
    expired = False
    tampered = False
    with transaction.atomic():
        approval = (
            Approval.objects.for_owner(owner).select_for_update().filter(pk=approval_id).first()
        )
        if approval is None:
            raise NotFound()
        if approval.state != ApprovalState.PENDING:
            # The same decision repeated (a retried request) is not an error.
            if approval.state == wanted or (
                wanted == ApprovalState.APPROVED and approval.state == ApprovalState.CONSUMED
            ):
                return approval
            raise ApprovalNotPendingError()
        if now >= approval.expires_at:
            _expire(approval, now)
            expired = True
        elif payload_sha256(approval.action_payload) != approval.payload_sha256:
            tampered = True
        elif decision == APPROVE and not hmac.compare_digest(
            echoed_sha256.lower(), approval.payload_sha256
        ):
            raise ValidationError(
                {"payload_sha256": ["Does not match the action awaiting approval."]}
            )
        else:
            approval.state = wanted
            approval.decided_at = now
            approval.decided_via_device = device
            approval.decision_factor = factor
            approval.save()
            record(
                f"approval.{wanted.value}",
                owner_id=owner.pk,
                actor=AuditActor.USER,
                device_id=device.pk,
                target_type="approval",
                target_id=approval.pk,
                detail={"tool": approval.tool_name, "factor": factor},
            )
            return approval
    if expired:
        raise ApprovalExpiredError()
    if tampered:
        record(
            "approval.integrity_failure",
            owner_id=owner.pk,
            actor=AuditActor.SYSTEM,
            target_type="approval",
            target_id=approval_id,
        )
        raise ApprovalIntegrityError()
    raise AssertionError("unreachable")  # pragma: no cover


def consume(
    approval_id: uuid.UUID, *, expected_payload: Mapping[str, Any], now: datetime
) -> Approval:
    """Spend an approval. Succeeds at most once per approval and only for the exact action.

    `expected_payload` is rebuilt by the caller from the step it is about to run, so a change
    to either the step or the approval row after the owner decided is caught here.
    """
    outcome: type[Exception] | None = None
    with transaction.atomic():
        approval = Approval.objects.select_for_update().get(pk=approval_id)
        if approval.state != ApprovalState.APPROVED:
            outcome = ApprovalNotApprovedError
        elif now >= approval.expires_at:
            _expire(approval, now)
            outcome = ApprovalExpiredError
        elif (
            payload_sha256(approval.action_payload) != approval.payload_sha256
            or payload_sha256(expected_payload) != approval.payload_sha256
        ):
            outcome = ApprovalIntegrityError
        else:
            approval.state = ApprovalState.CONSUMED
            approval.consumed_at = now
            approval.save(update_fields=["state", "consumed_at", "updated_at"])
            record(
                "approval.consumed",
                owner_id=approval.owner_id,
                actor=AuditActor.AGENT,
                target_type="approval",
                target_id=approval.pk,
                detail={"tool": approval.tool_name},
            )
            return approval
    if outcome is ApprovalIntegrityError:
        record(
            "approval.integrity_failure",
            owner_id=approval.owner_id,
            actor=AuditActor.SYSTEM,
            target_type="approval",
            target_id=approval_id,
        )
    raise cast("type[Exception]", outcome)()


def expire_due(now: datetime, *, workflow_id: uuid.UUID | None = None) -> list[uuid.UUID]:
    """Mark overdue pending or approved-but-unspent approvals expired.

    Returns the ids of workflows that should be resumed so the model can be told.
    """
    resume: list[uuid.UUID] = []
    queryset = Approval.objects.filter(state__in=_LIVE, expires_at__lte=now)
    if workflow_id is not None:
        queryset = queryset.filter(workflow_id=workflow_id)
    for approval_pk in list(queryset.values_list("pk", flat=True)):
        with transaction.atomic():
            approval = Approval.objects.select_for_update().get(pk=approval_pk)
            if approval.state in _LIVE and now >= approval.expires_at:
                _expire(approval, now)
                resume.append(approval.workflow_id)
    return resume


def cancel_for_workflow(workflow_id: uuid.UUID) -> int:
    """A cancelled workflow leaves no approval that could still be used."""
    cancelled = 0
    for approval in Approval.objects.select_for_update().filter(
        workflow_id=workflow_id, state__in=_LIVE
    ):
        approval.state = ApprovalState.CANCELLED
        approval.save(update_fields=["state", "updated_at"])
        cancelled += 1
    return cancelled

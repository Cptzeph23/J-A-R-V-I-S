"""Crash recovery (SRS-AGT-06). Safe to run at any time and any number of times.

A worker can die at any point. What it left behind is classified here:

- a step stuck in `running` whose invocation was recorded as succeeded: the effect happened,
  so the result is recorded and the workflow carries on;
- a read-only step stuck in `running`: re-running it is harmless, so it goes back to pending;
- a mutating step stuck in `running` with no recorded success: nobody can say whether the
  change happened, so the workflow becomes `needs_review` instead of guessing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q

from apps.approvals import services as approval_services
from apps.approvals.models import Approval, ApprovalState
from apps.workflows import services as wf_services
from apps.workflows.models import StepState, Workflow, WorkflowStep

from .models import InvocationStatus, ToolInvocation
from .planner import format_result
from .state_machine import TERMINAL, WorkflowState
from .tools.registry import ToolRegistry

STALE_AFTER = timedelta(seconds=60)


def settle_interrupted_steps(workflow: Workflow, registry: ToolRegistry, *, now: datetime) -> None:
    """Call only while holding the workflow's lease."""
    stuck = list(workflow.steps.filter(state=StepState.RUNNING).order_by("seq"))
    for step in stuck:
        tool = registry.get(step.tool_name)
        key = f"{workflow.pk}:{step.seq}"
        done = ToolInvocation.objects.filter(
            owner_id=workflow.owner_id,
            tool_name=step.tool_name,
            idempotency_key=key,
            status=InvocationStatus.SUCCEEDED,
        ).first()
        if done is not None and tool is not None:
            result = done.result if isinstance(done.result, dict) else {}
            _finish(
                step,
                StepState.SUCCEEDED,
                format_result(tool, workflow_id=workflow.pk, seq=step.seq, result=result),
                now,
            )
        elif tool is not None and not tool.mutating:
            WorkflowStep.objects.filter(pk=step.pk).update(state=StepState.PENDING, started_at=None)
        else:
            with transaction.atomic():
                _finish(
                    step,
                    StepState.FAILED,
                    "Error: this action was interrupted and may or may not have happened.",
                    now,
                    code="interrupted",
                )
                wf_services.transition(
                    workflow.pk,
                    WorkflowState.NEEDS_REVIEW,
                    error_code="interrupted_mutation",
                    error_message="An action was interrupted; please check your data.",
                    now=now,
                )
            return


def _finish(
    step: WorkflowStep, state: StepState, text: str, now: datetime, *, code: str = ""
) -> None:
    WorkflowStep.objects.filter(pk=step.pk).update(
        state=state,
        result_for_model=text,
        is_error=state is not StepState.SUCCEEDED,
        error_code=code,
        finished_at=now,
    )


def due_workflow_ids(now: datetime) -> list[uuid.UUID]:
    """Workflows that should be picked up again by a worker."""
    cutoff = now - STALE_AFTER
    unleased = Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lt=now)
    working = Workflow.objects.filter(
        unleased,
        state__in=[WorkflowState.CREATED, WorkflowState.PLANNING, WorkflowState.RUNNING],
        updated_at__lt=cutoff,
    )
    decided = Workflow.objects.filter(
        unleased,
        state=WorkflowState.AWAITING_APPROVAL,
        approvals__state__in=[ApprovalState.APPROVED, ApprovalState.REJECTED],
    )
    cancelling = Workflow.objects.filter(cancel_requested_at__isnull=False).exclude(
        state__in=[s.value for s in TERMINAL]
    )
    ids: set[uuid.UUID] = set()
    for queryset in (working, decided, cancelling):
        ids.update(queryset.values_list("pk", flat=True))
    return sorted(ids)


def expire_approvals(now: datetime) -> list[uuid.UUID]:
    """Expire overdue approvals; returns the workflows to wake so the model is told."""
    return sorted(set(approval_services.expire_due(now)))


def overdue_approval_exists(now: datetime) -> bool:
    return Approval.objects.filter(
        state__in=[ApprovalState.PENDING, ApprovalState.APPROVED], expires_at__lte=now
    ).exists()

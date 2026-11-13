"""Crash recovery (SRS-AGT-06): what a dead worker leaves behind is settled, never guessed."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.agent import recovery, tasks
from apps.agent.llm.fake import say
from apps.agent.models import InvocationStatus, ToolInvocation
from apps.approvals.models import Approval, ApprovalState
from apps.workflows.models import StepState, Workflow
from tests.agent_fixtures import decide, drive, pending_approval, start
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _crash_after_approval(owner) -> tuple[Workflow, Approval]:
    """Approved and consumed, step marked running, worker died before the effect."""
    approval = pending_approval(owner)
    decide(owner, approval)
    Approval.objects.filter(pk=approval.pk).update(state=ApprovalState.CONSUMED)
    workflow = Workflow.objects.get(pk=approval.workflow_id)
    workflow.steps.update(state=StepState.RUNNING)
    Workflow.objects.filter(pk=workflow.pk).update(state="running")
    workflow.refresh_from_db()
    return workflow, approval


def test_mutating_step_without_a_recorded_effect_needs_review_not_a_retry():
    owner = UserFactory()
    workflow, _ = _crash_after_approval(owner)
    llm = drive(workflow, [])
    assert workflow.state == "needs_review"
    assert workflow.error_code == "interrupted_mutation"
    assert workflow.steps.get().state == StepState.FAILED
    assert llm.requests == []
    from apps.finance.models import Transaction

    assert Transaction.objects.get().category_id is None


def test_mutating_step_with_a_recorded_effect_is_completed_not_repeated():
    owner = UserFactory()
    workflow, _ = _crash_after_approval(owner)
    step = workflow.steps.get()
    ToolInvocation.objects.create(
        owner=owner,
        tool_name=step.tool_name,
        idempotency_key=f"{workflow.pk}:{step.seq}",
        args_hash="x" * 64,
        status=InvocationStatus.SUCCEEDED,
        result={"transaction_id": "t", "category_id": "c"},
    )
    drive(workflow, [say("All set.")])
    assert workflow.state == "succeeded"
    assert workflow.steps.get().state == StepState.SUCCEEDED
    assert ToolInvocation.objects.count() == 1


def test_read_only_step_is_simply_run_again():
    owner = UserFactory()
    workflow = start(owner)
    from apps.workflows.models import WorkflowStep

    Workflow.objects.filter(pk=workflow.pk).update(state="running")
    WorkflowStep.objects.create(
        owner=owner,
        workflow=workflow,
        seq=1,
        turn=1,
        tool_call_id="c1",
        tool_name="finance_list_categories",
        args={},
        state=StepState.RUNNING,
    )
    workflow.refresh_from_db()
    drive(workflow, [say("Listed.")])
    assert workflow.state == "succeeded"
    assert workflow.steps.get().state == StepState.SUCCEEDED


def test_due_workflows_are_found_only_when_stale_and_unleased():
    owner = UserFactory()
    now = timezone.now()
    stale = start(owner)
    Workflow.objects.filter(pk=stale.pk).update(updated_at=now - timedelta(minutes=5))
    fresh = start(owner)
    leased = start(owner)
    Workflow.objects.filter(pk=leased.pk).update(
        updated_at=now - timedelta(minutes=5), lease_expires_at=now + timedelta(minutes=1)
    )
    ids = recovery.due_workflow_ids(now)
    assert stale.pk in ids
    assert fresh.pk not in ids
    assert leased.pk not in ids


def test_decided_approval_on_idle_workflow_is_picked_up():
    owner = UserFactory()
    approval = pending_approval(owner)
    assert approval.workflow_id not in recovery.due_workflow_ids(timezone.now())
    decide(owner, approval)
    assert approval.workflow_id in recovery.due_workflow_ids(timezone.now())


def test_expiry_sweep_expires_and_wakes(
    monkeypatch: pytest.MonkeyPatch, django_capture_on_commit_callbacks
):
    owner = UserFactory()
    approval = pending_approval(owner)
    Approval.objects.filter(pk=approval.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    woken: list[str] = []
    monkeypatch.setattr(tasks.run_workflow_task, "delay", lambda wid: woken.append(wid))
    with django_capture_on_commit_callbacks(execute=True):
        assert tasks.expire_approvals_task() == 1
    approval.refresh_from_db()
    assert approval.state == ApprovalState.EXPIRED
    assert woken == [str(approval.workflow_id)]
    assert tasks.expire_approvals_task() == 0


def test_recover_task_enqueues_due_workflows(monkeypatch: pytest.MonkeyPatch):
    owner = UserFactory()
    stale = start(owner)
    Workflow.objects.filter(pk=stale.pk).update(updated_at=timezone.now() - timedelta(minutes=5))
    woken: list[str] = []
    monkeypatch.setattr(tasks.run_workflow_task, "delay", lambda wid: woken.append(wid))
    assert tasks.recover_task() == 1
    assert woken == [str(stale.pk)]


def test_recovered_run_drives_a_stranded_workflow_to_completion():
    owner = UserFactory()
    workflow = start(owner)
    drive(workflow, [say("Recovered.")])
    assert workflow.state == "succeeded"

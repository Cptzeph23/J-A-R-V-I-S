"""Helpers for agent tests: start a run without a worker, drive it with a scripted model."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.agent.llm.fake import FakeLLM, Step
from apps.agent.orchestrator import run_workflow
from apps.agent.services import lifecycle, permissions
from apps.agent.tools.base import PERM_FINANCE_WRITE
from apps.approvals.models import Approval
from apps.conversations.models import Conversation
from apps.devices.models import Device
from apps.workflows.models import Workflow
from tests.factories import DeviceFactory


def start(owner: User, text: str = "hello", source: str = "text") -> Workflow:
    """Create the user message and workflow. The enqueue is dropped: tests drive it directly."""
    conversation = Conversation.objects.create(owner=owner)
    _, workflow = lifecycle.start_run(
        owner=owner, conversation=conversation, content=text, source=source
    )
    return workflow


def drive(
    workflow: Workflow, script: list[Step], *, now_fn: Callable[[], datetime] = timezone.now
) -> FakeLLM:
    llm = FakeLLM(script)
    run_workflow(workflow.pk, llm=llm, now_fn=now_fn)
    workflow.refresh_from_db()
    return llm


def allow_writes(owner: User) -> None:
    permissions.set_permissions(
        owner, [*permissions.granted_permissions(owner), PERM_FINANCE_WRITE]
    )


def decide(owner: User, approval: Approval, decision: str = "approve", **kw: Any) -> Approval:
    device: Device = kw.pop("device", None) or DeviceFactory(owner=owner)
    return lifecycle.decide_approval(
        owner=owner,
        approval_id=approval.pk,
        decision=decision,
        echoed_sha256=kw.pop("echoed_sha256", approval.payload_sha256),
        device=device,
        factor=kw.pop("factor", "tap"),
    )


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that forgets to supply a model must fail, not reach a provider."""

    def refuse() -> Any:
        raise AssertionError("test reached the real LLM factory")

    import apps.agent.orchestrator as orchestrator

    monkeypatch.setattr(orchestrator, "get_llm_client", refuse)


def pending_approval(owner: User) -> Approval:
    """A workflow waiting on an approval for a category change."""
    from apps.agent.llm.fake import call
    from tests.factories import CategoryFactory, TransactionFactory

    allow_writes(owner)
    tx = TransactionFactory(owner=owner)
    category = CategoryFactory(owner=owner)
    workflow = start(owner)
    drive(
        workflow,
        [
            call(
                "finance_set_transaction_category",
                {"transaction_id": str(tx.pk), "category_id": str(category.pk)},
            )
        ],
    )
    return Approval.objects.get(workflow=workflow)

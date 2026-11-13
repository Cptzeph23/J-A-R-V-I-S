from __future__ import annotations

import pytest

from apps.agent.models import InvocationStatus, ToolInvocation
from apps.agent.services.executor import ToolError, execute_tool
from apps.agent.tools.base import PERM_FINANCE_READ, Tool, ToolContext, ToolInput
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


class Args(ToolInput):
    n: int = 1


def _tool(handler, name: str = "test_counter") -> Tool:
    return Tool(
        name=name,
        description="test",
        input_model=Args,
        handler=handler,
        permission=PERM_FINANCE_READ,
        mutating=False,
    )


def _ctx(owner, key: str = "wf:1") -> ToolContext:
    import uuid

    return ToolContext(owner=owner, workflow_id=uuid.uuid4(), step_seq=1, idempotency_key=key)


def test_same_key_runs_once_and_replays():
    owner = UserFactory()
    calls: list[int] = []

    def handler(ctx, args):
        calls.append(args.n)
        return {"n": args.n}

    tool = _tool(handler)
    first = execute_tool(tool, Args(n=3), _ctx(owner))
    again = execute_tool(tool, Args(n=3), _ctx(owner))
    assert first.ok
    assert again.replayed
    assert again.result == {"n": 3}
    assert calls == [3]
    assert ToolInvocation.objects.count() == 1


def test_same_key_with_different_arguments_is_refused():
    owner = UserFactory()
    tool = _tool(lambda ctx, args: {"n": args.n})
    execute_tool(tool, Args(n=1), _ctx(owner))
    outcome = execute_tool(tool, Args(n=2), _ctx(owner))
    assert not outcome.ok
    assert outcome.error_code == "idempotency_key_reused"


def test_tool_error_is_reported_and_recorded_without_effect():
    owner = UserFactory()

    def handler(ctx, args):
        raise ToolError("not_found", "No such thing.")

    outcome = execute_tool(_tool(handler), Args(), _ctx(owner))
    assert (outcome.ok, outcome.error_code, outcome.message) == (
        False,
        "not_found",
        "No such thing.",
    )
    row = ToolInvocation.objects.get()
    assert row.status == InvocationStatus.FAILED
    assert row.error_code == "not_found"


def test_unexpected_exception_does_not_leak_details_and_can_be_retried():
    owner = UserFactory()
    state = {"fail": True}

    def handler(ctx, args):
        if state["fail"]:
            raise RuntimeError("secret 0712345678")
        return {"ok": True}

    tool = _tool(handler)
    bad = execute_tool(tool, Args(), _ctx(owner))
    assert bad.error_code == "tool_crashed"
    assert "0712345678" not in bad.message
    state["fail"] = False
    good = execute_tool(tool, Args(), _ctx(owner))
    assert good.ok
    row = ToolInvocation.objects.get()
    assert row.status == InvocationStatus.SUCCEEDED
    assert row.attempts == 2


def test_unstorable_result_rolls_back_the_effect():
    owner = UserFactory()
    outcome = execute_tool(_tool(lambda ctx, args: {"bad": object()}), Args(), _ctx(owner))
    assert not outcome.ok
    assert not ToolInvocation.objects.filter(status=InvocationStatus.SUCCEEDED).exists()


def test_keys_are_scoped_per_owner():
    a, b = UserFactory(), UserFactory()
    tool = _tool(lambda ctx, args: {"owner": str(ctx.owner.pk)})
    ra = execute_tool(tool, Args(), _ctx(a))
    rb = execute_tool(tool, Args(), _ctx(b))
    assert ra.result != rb.result
    assert ToolInvocation.objects.count() == 2

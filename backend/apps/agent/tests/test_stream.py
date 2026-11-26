"""Server-sent events for a workflow."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from apps.agent.api import stream
from apps.agent.llm.fake import call, say
from apps.approvals.models import Approval
from apps.workflows.models import Workflow
from tests.agent_fixtures import drive, pending_approval, start
from tests.auth_fixtures import AuthedClient

pytestmark = pytest.mark.django_db


def parse(chunks: list[bytes]) -> list[tuple[str, dict]]:
    """(event name, data) for each event; comments and retry hints are skipped."""
    events = []
    for block in b"".join(chunks).decode().split("\n\n"):
        lines = [x for x in block.split("\n") if x]
        name = next((x[7:] for x in lines if x.startswith("event: ")), None)
        data = next((x[6:] for x in lines if x.startswith("data: ")), None)
        if name:
            events.append((name, json.loads(data or "{}")))
    return events


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps = 0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds


def run(owner, workflow, *, on_sleep: Callable[[int], None] | None = None, **kw) -> list[bytes]:
    fake = FakeTime()

    def sleep(seconds: float) -> None:
        fake.sleep(seconds)
        if on_sleep:
            on_sleep(fake.sleeps)

    return list(stream.stream(owner, workflow.pk, sleep=sleep, clock=fake.clock, **kw))


def test_a_finished_workflow_sends_one_snapshot_then_done(owner_a):
    wf = start(owner_a, "hi")
    drive(wf, [say("Hello there")])
    events = parse(run(owner_a, wf))
    assert [name for name, _ in events] == ["snapshot", "done"]
    snap = events[0][1]
    assert snap["workflow"]["state"] == "succeeded"
    assert snap["answer"]["content"] == "Hello there"
    assert snap["answer"]["role"] == "assistant"
    assert snap["approvals"] == []
    assert events[1][1] == {"state": "succeeded"}


def test_snapshots_are_sent_only_when_something_changes(owner_a):
    wf = start(owner_a, "hi")  # created, waiting for a worker

    def advance(n: int) -> None:
        if n == 3:
            drive(wf, [say("Done")])

    chunks = run(owner_a, wf, on_sleep=advance)
    names = [name for name, _ in parse(chunks)]
    assert names == ["snapshot", "snapshot", "done"]  # created, then finished; polls 1 and 2 silent


def test_a_pending_approval_is_included_with_the_exact_payload_hash(owner_a):
    approval = pending_approval(owner_a)
    wf = Workflow.objects.get(pk=approval.workflow_id)
    events = parse(run(owner_a, wf, max_seconds=3))
    snap = next(data for name, data in events if name == "snapshot")
    assert snap["workflow"]["state"] == "awaiting_approval"
    assert [a["id"] for a in snap["approvals"]] == [str(approval.pk)]
    assert snap["approvals"][0]["payload_sha256"] == approval.payload_sha256
    assert events[-1][0] == "timeout"


def test_the_stream_is_bounded_and_sends_heartbeats(owner_a):
    wf = start(owner_a, "hi")
    chunks = run(owner_a, wf, max_seconds=40, heartbeat_seconds=15)
    raw = b"".join(chunks)
    assert raw.startswith(b"retry: 3000")
    assert raw.count(b": ping") == 2  # at 15 s and 30 s
    assert parse(chunks)[-1][0] == "timeout"


def test_a_workflow_that_vanishes_ends_with_gone(owner_a):
    wf = start(owner_a, "hi")

    def vanish(_seconds: float) -> None:
        Workflow.objects.all().delete()

    gen = stream.stream(owner_a, wf.pk, sleep=vanish)
    next(gen)  # retry hint
    assert parse([next(gen)])[0][0] == "snapshot"
    assert parse([next(gen)])[0][0] == "gone"


def test_another_owners_workflow_is_never_streamed(owner_a, owner_b):
    wf = start(owner_a, "secret")
    assert parse(run(owner_b, wf)) == [("gone", {})]


# --- HTTP ----------------------------------------------------------------------------------


def test_endpoint_streams_with_the_right_headers(owner_a, client_a: AuthedClient):
    wf = start(owner_a, "hi")
    drive(wf, [say("ok")])
    response = client_a.get(f"/api/v1/agent/workflows/{wf.pk}/stream")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/event-stream")
    assert response["Cache-Control"] == "no-cache, no-transform"
    assert response["X-Accel-Buffering"] == "no"
    events = parse(list(response.streaming_content))  # type: ignore[attr-defined]
    assert [name for name, _ in events] == ["snapshot", "done"]


def test_endpoint_hides_other_owners_and_requires_authentication(
    owner_a, client_b: AuthedClient, db
):
    from rest_framework.test import APIClient

    wf = start(owner_a, "hi")
    url = f"/api/v1/agent/workflows/{wf.pk}/stream"
    assert client_b.get(url).status_code == 404
    assert APIClient().get(url).status_code in (401, 403)


def test_a_cancelled_workflow_ends_the_stream(owner_a):
    from apps.agent.services import lifecycle

    wf = start(owner_a, "hi")
    lifecycle.request_cancel(owner=owner_a, workflow_id=wf.pk)
    events = parse(run(owner_a, wf))
    assert events[-1] == ("done", {"state": "cancelled"})


def test_tool_steps_are_visible_while_running(owner_a):
    wf = start(owner_a, "hi")
    drive(wf, [call("dashboard_summary", {"range": "today"}), say("x")])
    snap = parse(run(owner_a, wf))[0][1]
    assert [step["tool_name"] for step in snap["workflow"]["steps"]] == ["dashboard_summary"]
    assert Approval.objects.count() == 0

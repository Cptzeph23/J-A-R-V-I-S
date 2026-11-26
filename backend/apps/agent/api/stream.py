"""Server-sent events for one workflow.

The language model runs in a background worker and returns whole answers, so there are no
tokens to stream. What the client needs is to see the run move: planning, a step running, an
approval waiting, the answer arriving. This stream sends a full `snapshot` whenever any of that
changes (and nothing while it has not), a `done` event when the workflow reaches a terminal
state, and a comment heartbeat so proxies keep the connection open.

Each stream is bounded: after `max_seconds` it ends with a `timeout` event and the client
reconnects, receiving the current snapshot first. A reconnect therefore never misses state.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

from apps.accounts.models import User
from apps.agent.state_machine import TERMINAL
from apps.approvals.models import Approval, ApprovalState
from apps.conversations.models import Message
from apps.workflows.models import Workflow

from . import serializers as s

POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 15.0
MAX_SECONDS = 55.0
RETRY_MS = 3000
_TERMINAL_VALUES = frozenset(state.value for state in TERMINAL)


def _event(name: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, separators=(",", ":"), default=str)
    return f"event: {name}\ndata: {payload}\n\n".encode()


def snapshot(owner: User, workflow_id: uuid.UUID) -> dict[str, Any] | None:
    """The workflow, its pending approvals and its answer; None when it is not the owner's."""
    workflow = (
        Workflow.objects.for_owner(owner).prefetch_related("steps").filter(pk=workflow_id).first()
    )
    if workflow is None:
        return None
    approvals = Approval.objects.for_owner(owner).filter(
        workflow=workflow, state=ApprovalState.PENDING
    )
    answer = None
    if workflow.answer_message_id:
        message = Message.objects.for_owner(owner).filter(pk=workflow.answer_message_id).first()
        answer = s.MessageSerializer(message).data if message else None
    return {
        "workflow": s.WorkflowDetailSerializer(workflow).data,
        "approvals": s.ApprovalSerializer(approvals, many=True).data,
        "answer": answer,
    }


def stream(
    owner: User,
    workflow_id: uuid.UUID,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    poll_seconds: float = POLL_SECONDS,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    max_seconds: float = MAX_SECONDS,
) -> Iterator[bytes]:
    yield f"retry: {RETRY_MS}\n\n".encode()
    started = last_sent = clock()
    previous: str | None = None
    while True:
        current = snapshot(owner, workflow_id)
        if current is None:
            yield _event("gone", {})
            return
        encoded = json.dumps(current, sort_keys=True, default=str)
        if encoded != previous:
            previous = encoded
            last_sent = clock()
            yield _event("snapshot", current)
        if current["workflow"]["state"] in _TERMINAL_VALUES:
            yield _event("done", {"state": current["workflow"]["state"]})
            return
        now = clock()
        if now - started >= max_seconds:
            yield _event("timeout", {})
            return
        if now - last_sent >= heartbeat_seconds:
            last_sent = now
            yield b": ping\n\n"
        sleep(poll_seconds)

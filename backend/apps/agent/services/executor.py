"""Run one tool call exactly once (SRS-AGT-05, threat T12).

The handler's effects and the `ToolInvocation` row that records them commit in one database
transaction, so "the effect happened" and "the key is recorded" can never disagree. A retry
with the same key returns the recorded result and does not run the handler again. A failed
attempt rolls everything back and leaves a `failed` row, so a retry is safe.

A future tool whose effect lives outside the database (sending mail, calling a provider) cannot
be rolled back this way. Such a tool must commit a `running` invocation first, pass the
idempotency key to the provider, and recovery treats a leftover `running` row as needs_review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import psycopg.errors
import structlog
from django.db import OperationalError, connection, transaction

from apps.approvals.hashing import payload_sha256

from ..models import InvocationStatus, ToolInvocation
from ..tools.base import Tool, ToolContext, ToolInput

logger = structlog.get_logger(__name__)


class ToolError(Exception):
    """A tool refusing or failing in a way the model and owner may be told about."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    ok: bool
    result: dict[str, Any] | None = None
    error_code: str = ""
    message: str = ""
    replayed: bool = False


def _fail(owner_id: Any, tool: Tool, ctx: ToolContext, args_hash: str, code: str) -> None:
    """Record a failed attempt outside the rolled-back transaction."""
    row, created = ToolInvocation.objects.get_or_create(
        owner_id=owner_id,
        tool_name=tool.name,
        idempotency_key=ctx.idempotency_key,
        defaults={
            "args_hash": args_hash,
            "status": InvocationStatus.FAILED,
            "error_code": code,
        },
    )
    if not created and row.status != InvocationStatus.SUCCEEDED:
        row.attempts += 1
        row.status = InvocationStatus.FAILED
        row.error_code = code
        row.save(update_fields=["attempts", "status", "error_code", "updated_at"])


def execute_tool(tool: Tool, args: ToolInput, ctx: ToolContext) -> ToolOutcome:
    args_hash = payload_sha256(args.model_dump(mode="json"))
    failure: ToolOutcome
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL statement_timeout = {int(tool.timeout_seconds) * 1000}")
            existing = (
                ToolInvocation.objects.select_for_update()
                .filter(owner=ctx.owner, tool_name=tool.name, idempotency_key=ctx.idempotency_key)
                .first()
            )
            if existing is not None and existing.args_hash != args_hash:
                return ToolOutcome(
                    False,
                    error_code="idempotency_key_reused",
                    message="That call does not match the one previously run under this key.",
                )
            if existing is not None and existing.status == InvocationStatus.SUCCEEDED:
                replay = existing.result if isinstance(existing.result, dict) else {}
                return ToolOutcome(True, replay, replayed=True)

            result = dict(tool.handler(ctx, args))
            json.dumps(result)  # must be storable; a TypeError here rolls the effect back
            if existing is None:
                ToolInvocation.objects.create(
                    owner=ctx.owner,
                    tool_name=tool.name,
                    idempotency_key=ctx.idempotency_key,
                    args_hash=args_hash,
                    status=InvocationStatus.SUCCEEDED,
                    result=result,
                )
            else:
                existing.attempts += 1
                existing.status = InvocationStatus.SUCCEEDED
                existing.result = result
                existing.error_code = ""
                existing.save()
            return ToolOutcome(True, result)
    except ToolError as exc:
        failure = ToolOutcome(False, error_code=exc.code, message=exc.message)
    except OperationalError as exc:
        timed_out = isinstance(exc.__cause__, psycopg.errors.QueryCanceled)
        logger.warning("tool_db_error", tool=tool.name, timed_out=timed_out)
        failure = ToolOutcome(
            False,
            error_code="tool_timeout" if timed_out else "tool_crashed",
            message="The tool took too long." if timed_out else "The tool failed unexpectedly.",
        )
    except Exception:
        # Logged with the traceback but never with arguments or results (personal data).
        logger.exception("tool_crashed", tool=tool.name)
        failure = ToolOutcome(
            False, error_code="tool_crashed", message="The tool failed unexpectedly."
        )
    _fail(ctx.owner.pk, tool, ctx, args_hash, failure.error_code)
    return failure

"""Throwaway endpoints used only by test_idempotency.py (via override_settings)."""

import datetime
import uuid
from typing import Any

from django.urls import path
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.recorder import record
from apps.common.idempotency import execute_idempotent

CALLS: list[str] = []


class _Base(APIView):
    scope = "test.create"

    def post(self, request: Request) -> Response:
        return execute_idempotent(request, scope=self.scope, handler=lambda: self.handle(request))

    def handle(self, request: Request) -> Response:
        CALLS.append(self.scope)
        record("test.created", owner_id=request.user.pk, detail={"n": len(CALLS)})
        return Response(
            {
                "id": uuid.UUID(int=7),
                "when": datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.UTC),
                "echo": request.data,
            },
            status=status.HTTP_201_CREATED,
        )


class CreateView(_Base):
    pass


class OtherScopeView(_Base):
    scope = "test.other"


class FailingView(_Base):
    scope = "test.failing"

    def handle(self, request: Request) -> Response:
        record("test.created", owner_id=request.user.pk)
        if isinstance(request.data, dict) and request.data.get("fail"):
            raise ValidationError({"fail": ["requested failure"]})
        return Response({"ok": True}, status=status.HTTP_201_CREATED)


class NonSuccessView(_Base):
    scope = "test.nonsuccess"

    def handle(self, request: Request) -> Response:
        record("test.created", owner_id=request.user.pk)
        body: dict[str, Any] = {"detail": "accepted for later"}
        return Response(body, status=status.HTTP_400_BAD_REQUEST)


urlpatterns = [
    path("create", CreateView.as_view()),
    path("other", OtherScopeView.as_view()),
    path("failing", FailingView.as_view()),
    path("nonsuccess", NonSuccessView.as_view()),
]

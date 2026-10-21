

from __future__ import annotations

from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django.urls import path
from rest_framework import exceptions, serializers
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.errors import (
    ApprovalRequiredError,
    ConflictError,
    DegradedError,
    ProviderError,
)


class DenyAll(BasePermission):
    def has_permission(self, request: Request, view: Any) -> bool:
        return False


class _Open(APIView):
    permission_classes = [AllowAny]


class Payload(serializers.Serializer[Any]):
    amount = serializers.IntegerField()
    name = serializers.CharField()


class ValidationView(_Open):
    def post(self, request: Request) -> Response:
        serializer = Payload(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response({"ok": True})


class DefaultDeniedView(APIView):
    def get(self, request: Request) -> Response:
        return Response({"ok": True})


class ForbiddenView(APIView):
    permission_classes = [DenyAll]

    def get(self, request: Request) -> Response:
        return Response({})


class UnauthenticatedView(_Open):
    def get(self, request: Request) -> Response:
        raise exceptions.NotAuthenticated()


class NotFoundView(_Open):
    def get(self, request: Request) -> Response:
        raise Http404


class DjangoForbiddenView(_Open):
    def get(self, request: Request) -> Response:
        raise DjangoPermissionDenied


class ConflictView(_Open):
    def get(self, request: Request) -> Response:
        raise ConflictError("Duplicate key.", details={"field": "key"})


class ApprovalView(_Open):
    def get(self, request: Request) -> Response:
        raise ApprovalRequiredError("11111111-1111-1111-1111-111111111111")


class ProviderView(_Open):
    def get(self, request: Request) -> Response:
        raise ProviderError()


class DegradedView(_Open):
    def get(self, request: Request) -> Response:
        raise DegradedError()


class ThrottledView(_Open):
    def get(self, request: Request) -> Response:
        raise exceptions.Throttled(wait=7)


class BoomView(_Open):
    def get(self, request: Request) -> Response:
        raise RuntimeError("secret-internal-detail")


class GetOnlyView(_Open):
    def get(self, request: Request) -> Response:
        return Response({})


urlpatterns = [
    path("validation", ValidationView.as_view()),
    path("default-denied", DefaultDeniedView.as_view()),
    path("forbidden", ForbiddenView.as_view()),
    path("unauthenticated", UnauthenticatedView.as_view()),
    path("not-found", NotFoundView.as_view()),
    path("django-forbidden", DjangoForbiddenView.as_view()),
    path("conflict", ConflictView.as_view()),
    path("approval", ApprovalView.as_view()),
    path("provider", ProviderView.as_view()),
    path("degraded", DegradedView.as_view()),
    path("throttled", ThrottledView.as_view()),
    path("boom", BoomView.as_view()),
    path("get-only", GetOnlyView.as_view()),
]

handler400 = "apps.common.errors.bad_request_view"
handler403 = "apps.common.errors.permission_denied_view"
handler404 = "apps.common.errors.not_found_view"
handler500 = "apps.common.errors.server_error_view"

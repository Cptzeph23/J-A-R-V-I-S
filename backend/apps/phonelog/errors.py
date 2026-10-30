"""Errors for phone activity collection."""

from __future__ import annotations

from rest_framework import status

from apps.common.errors import ApiError, ConflictError


class CollectionUnsupportedError(ApiError):
    """Only an Android device can collect call and SMS data (ADR-010, FR-PHN-004)."""

    status_code = status.HTTP_403_FORBIDDEN
    default_code = "collection_unsupported"
    default_detail = "This device cannot upload phone activity."


class CollectionOffError(ConflictError):
    default_code = "collection_disabled"
    default_detail = "Phone activity collection is turned off for this device."


class CollectionPausedError(ConflictError):
    default_code = "collection_paused"
    default_detail = "Phone activity collection is paused for this device."

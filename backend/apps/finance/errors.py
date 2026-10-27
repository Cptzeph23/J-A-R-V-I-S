"""Domain errors specific to finance, mapped onto the standard error envelope."""

from __future__ import annotations

from rest_framework import status

from apps.common.errors import ApiError, ConflictError


class ImportFileError(ApiError):
    """The uploaded file cannot be used at all (as opposed to some rows being rejected)."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid_import_file"
    default_detail = "The import file could not be used."

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message, details={"reason": reason})


class DuplicateTransactionError(ConflictError):
    default_code = "duplicate_transaction"
    default_detail = "A transaction with this reference already exists in this account."

"""Pure finance rules shared by manual entry, importers and reports. No database access."""

from __future__ import annotations

import re
import unicodedata

from .models import INFLOW_KINDS, OUTFLOW_KINDS, TransactionKind

_WHITESPACE = re.compile(r"\s+")

# Hints accepted from CSV `type` columns and API payloads.
KIND_HINTS = frozenset({"income", "expense", "transfer", "fee", "refund"})

MAX_DESCRIPTION_LENGTH = 500
MAX_MERCHANT_LENGTH = 200
MAX_REFERENCE_LENGTH = 64


class KindError(ValueError):
    """The kind hint and the amount's sign cannot describe the same transaction."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def normalize_text(value: str) -> str:
    """NFKC, casefold, collapse whitespace. Used for matching and fingerprints only."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip().casefold()


def normalize_merchant_name(value: str) -> str:
    return normalize_text(value)


def resolve_kind(hint: str | None, amount_minor: int) -> str:
    """Return the TransactionKind for an amount, validating any explicit hint.

    Without a hint the sign decides (positive income, negative expense). Transfers take
    their direction from the sign. Any hint that contradicts the sign is an error, never
    silently corrected, because a flipped sign would corrupt every total.
    """
    if amount_minor == 0:
        raise KindError("zero_amount", "Amount must not be zero.")
    inflow = amount_minor > 0
    cleaned = (hint or "").strip().casefold()
    if not cleaned:
        return TransactionKind.INCOME.value if inflow else TransactionKind.EXPENSE.value
    if cleaned not in KIND_HINTS:
        raise KindError("invalid_type", f"Unknown transaction type {hint!r}.")
    if cleaned == "transfer":
        return TransactionKind.TRANSFER_IN.value if inflow else TransactionKind.TRANSFER_OUT.value
    if cleaned in INFLOW_KINDS and not inflow:
        raise KindError("sign_mismatch", f"{cleaned} must be a positive amount.")
    if cleaned in OUTFLOW_KINDS and inflow:
        raise KindError("sign_mismatch", f"{cleaned} must be a negative amount.")
    return cleaned

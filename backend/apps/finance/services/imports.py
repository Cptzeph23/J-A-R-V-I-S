"""CSV import: parse, deduplicate, categorize, persist (SRS-FIN-02/03).

Policies:
- atomic: if any row is rejected, nothing is stored and the batch is recorded as failed.
- partial: valid rows are stored, rejected rows are listed in the report.
Duplicates (same fingerprint already stored as a live row, or repeated inside the file) are
counted and skipped under both policies; they are not errors.

The audit entry carries counts and the file hash only. Row contents never enter the log.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.recorder import record

from ..categorization import Rule, match_rule
from ..domain import normalize_merchant_name
from ..errors import ImportFileError
from ..fingerprint import import_fingerprint
from ..importers.csv_importer import (
    ColumnMapping,
    CsvFileError,
    CsvOptions,
    ParsedRow,
    RowRejection,
    parse_csv,
)
from ..models import (
    CategoryRule,
    CategorySource,
    FinancialAccount,
    ImportBatch,
    ImportPolicy,
    ImportSource,
    ImportStatus,
    Transaction,
    Verification,
)
from .taxonomy import ensure_merchants
from .transactions import owner_zone

MAX_REPORTED_REJECTIONS = 200
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
DATE_FORMATS = frozenset(
    {
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
    }
)


@dataclass(frozen=True, slots=True)
class ImportRequest:
    account: FinancialAccount
    content: bytes
    file_name: str
    date_format: str
    amount_mode: str
    policy: str
    mapping: ColumnMapping


def _rules(owner: User) -> list[Rule]:
    return [
        Rule(r.match_type, r.pattern, r.category_id, r.priority)
        for r in CategoryRule.objects.for_owner(owner)
    ]


def _build_rejections(items: list[RowRejection]) -> list[dict[str, Any]]:
    return [
        {"row": r.row_number, "reason": r.reason, "detail": r.detail}
        for r in items[:MAX_REPORTED_REJECTIONS]
    ]


def _fingerprint(owner: User, account: FinancialAccount, row: ParsedRow) -> str:
    return import_fingerprint(
        owner_id=owner.pk,
        account_id=account.pk,
        posted_on=row.posted_on,
        amount_minor=row.amount_minor,
        currency=row.currency,
        description=row.description,
        provider_ref=row.provider_ref,
    )


def run_csv_import(owner: User, req: ImportRequest, *, today: date | None = None) -> ImportBatch:
    if req.date_format not in DATE_FORMATS:
        raise ImportFileError("invalid_options", "Unsupported date_format.")
    options = CsvOptions(
        account_currency=req.account.currency,
        timezone=owner_zone(owner),
        today=today or timezone.localdate(timezone=owner_zone(owner)),
        date_format=req.date_format,
        amount_mode=req.amount_mode,
        mapping=req.mapping,
    )
    try:
        parsed = parse_csv(req.content, options)
    except CsvFileError as exc:
        raise ImportFileError(exc.code, exc.detail) from exc

    file_hash = hashlib.sha256(req.content).hexdigest()
    rejections = parsed.rejections
    rejection_reasons = Counter(r.reason for r in rejections)
    atomic_blocked = req.policy == ImportPolicy.ATOMIC and bool(rejections)

    with transaction.atomic():
        imported = duplicates = 0
        if not atomic_blocked:
            imported, duplicates = _store(owner, req.account, parsed.rows)
        status = _status(atomic_blocked, bool(rejections))
        batch = ImportBatch.objects.create(
            owner=owner,
            account=req.account,
            source=ImportSource.CSV,
            policy=req.policy,
            status=status,
            file_name=req.file_name[:255],
            file_sha256=file_hash,
            rows_total=parsed.total_rows,
            rows_imported=imported,
            rows_duplicate=duplicates,
            rows_rejected=len(rejections),
            report={
                "rejections": _build_rejections(rejections),
                "rejections_truncated": len(rejections) > MAX_REPORTED_REJECTIONS,
                "rejection_reasons": dict(sorted(rejection_reasons.items())),
            },
        )
        record(
            "finance.import_completed"
            if status != ImportStatus.FAILED
            else "finance.import_failed",
            owner_id=owner.pk,
            target_type="import_batch",
            target_id=batch.pk,
            detail={
                "rows_total": parsed.total_rows,
                "rows_imported": imported,
                "rows_duplicate": duplicates,
                "rows_rejected": len(rejections),
                "file_sha256": file_hash,
                "policy": req.policy,
            },
        )
    return batch


def _status(blocked: bool, any_rejected: bool) -> str:
    if blocked:
        return ImportStatus.FAILED
    return ImportStatus.COMPLETED_WITH_REJECTIONS if any_rejected else ImportStatus.COMPLETED


def _store(owner: User, account: FinancialAccount, rows: list[ParsedRow]) -> tuple[int, int]:
    """Insert new rows; return (imported, duplicates). Must run inside a transaction."""
    prepared: dict[str, ParsedRow] = {}
    in_file_duplicates = 0
    for row in rows:
        fingerprint = _fingerprint(owner, account, row)
        if fingerprint in prepared:
            in_file_duplicates += 1
        else:
            prepared[fingerprint] = row
    if not prepared:
        return 0, in_file_duplicates

    existing = set(
        Transaction.objects.live_for_owner(owner)
        .filter(account=account, fingerprint__in=list(prepared))
        .values_list("fingerprint", flat=True)
    )
    fresh = {fp: row for fp, row in prepared.items() if fp not in existing}
    merchants = ensure_merchants(owner, [r.merchant_name for r in fresh.values()])
    rules = _rules(owner)

    to_create: list[Transaction] = []
    for fingerprint, row in fresh.items():
        merchant = (
            merchants.get(normalize_merchant_name(row.merchant_name)) if row.merchant_name else None
        )
        # Match on the merchant first (most specific), then on the description.
        hit = match_rule(rules, row.merchant_name, row.description)
        to_create.append(
            Transaction(
                owner=owner,
                account=account,
                posted_at=row.posted_at,
                posted_on=row.posted_on,
                has_time=row.has_time,
                amount_minor=row.amount_minor,
                currency=row.currency,
                kind=row.kind,
                description=row.description,
                merchant=merchant,
                category_id=hit.category_id if hit else None,
                category_source=CategorySource.RULE if hit else CategorySource.NONE,
                provider_ref=row.provider_ref,
                fingerprint=fingerprint,
                verification=Verification.VERIFIED,
            )
        )
    # ON CONFLICT DO NOTHING: a concurrent import of the same file loses the race
    # harmlessly instead of failing; the unique partial index is the final arbiter.
    Transaction.objects.bulk_create(to_create, ignore_conflicts=True)
    # Primary keys are generated client-side, so the rows that really landed are exactly
    # those whose pk now exists; the rest lost a race and count as duplicates.
    imported = Transaction.objects.filter(pk__in=[t.pk for t in to_create]).count()
    duplicates = in_file_duplicates + len(existing) + (len(to_create) - imported)
    return imported, duplicates

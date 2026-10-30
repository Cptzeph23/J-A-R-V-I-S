"""Financial SMS: allowlist, parse, record (SRS-FIN-02, threat T14).

What is kept of a message: the sender id, a SHA-256 of the body (dedup), the parse outcome and
the transaction it produced. The text itself is never stored (encrypted storage arrives with
the field-encryption module in Phase 6). Transactions created from an SMS are `unverified`
until a statement import contains the same M-PESA code, because an SMS can be forged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.accounts.models import User
from apps.audit.recorder import record
from apps.devices.models import Device
from apps.finance.categorization import match_rule
from apps.finance.domain import normalize_merchant_name
from apps.finance.fingerprint import import_fingerprint
from apps.finance.models import (
    CategorySource,
    FinancialAccount,
    Transaction,
    TransactionKind,
    Verification,
)
from apps.finance.services.accounts import mpesa_account, record_reported_balance_if_newer
from apps.finance.services.taxonomy import ensure_merchants, load_rules

from ..models import CollectionSettings, FinancialSmsRecord, SmsParseStatus
from ..parsers.allowlist import is_allowed_sender
from ..parsers.mpesa import FulizaNotice, MpesaPayment, ParseFailure, normalize_body, parse_mpesa
from .collection import require_collecting, touch_upload

# M-PESA prints Kenyan wall-clock time whatever zone the owner is in today.
MPESA_ZONE = ZoneInfo("Africa/Nairobi")
CURRENCY = "KES"
_UNPARSED_REASONS = frozenset({"no_template_matched", "empty_body"})


@dataclass(slots=True)
class SmsOutcome:
    source_record_id: str
    status: str  # parsed | unparsed | rejected | duplicate
    template: str = ""
    reason: str = ""


@dataclass(slots=True)
class SmsBatchResult:
    received: int = 0
    parsed: int = 0
    unparsed: int = 0
    rejected: int = 0
    duplicates: int = 0
    transactions_created: int = 0
    results: list[SmsOutcome] = field(default_factory=list)


def _create_transaction(
    owner: User,
    account: FinancialAccount,
    *,
    posted_at: datetime,
    kind: str,
    amount_minor: int,
    description: str,
    merchant_name: str,
    provider_ref: str,
    rules: list[Any],
) -> tuple[Transaction, bool]:
    """Create one transaction, or return the one that already holds this reference."""
    owner_zone = ZoneInfo(owner.timezone)
    fingerprint = import_fingerprint(
        owner_id=owner.pk,
        account_id=account.pk,
        posted_on=posted_at.astimezone(owner_zone).date(),
        amount_minor=amount_minor,
        currency=CURRENCY,
        description=description,
        provider_ref=provider_ref,
    )
    existing = Transaction.objects.live_for_owner(owner).filter(
        account=account, fingerprint=fingerprint
    )
    found = existing.first()
    if found is not None:
        return found, False
    merchant = (
        ensure_merchants(owner, [merchant_name]).get(normalize_merchant_name(merchant_name))
        if merchant_name
        else None
    )
    hit = match_rule(rules, merchant_name, description)
    try:
        with transaction.atomic():
            tx = Transaction.objects.create(
                owner=owner,
                account=account,
                posted_at=posted_at.astimezone(UTC),
                posted_on=posted_at.astimezone(owner_zone).date(),
                has_time=True,
                amount_minor=amount_minor,
                currency=CURRENCY,
                kind=kind,
                description=description[:500],
                merchant=merchant,
                category_id=hit.category_id if hit else None,
                category_source=CategorySource.RULE if hit else CategorySource.NONE,
                provider_ref=provider_ref,
                fingerprint=fingerprint,
                verification=Verification.UNVERIFIED,
            )
            record(
                "finance.transaction_created",
                owner_id=owner.pk,
                target_type="transaction",
                target_id=tx.pk,
                detail={"source": "mpesa_sms", "kind": kind},
            )
    except IntegrityError:
        # Lost a race with another upload of the same message: use the winner's row.
        return existing.get(), False
    return tx, True


def _describe(payment: MpesaPayment) -> str:
    if payment.template == "mpesa_paid":
        return f"Paid to {payment.counterparty}"
    if payment.template == "mpesa_paybill":
        return f"Paid to {payment.counterparty} account {payment.account_ref}"
    return f"Sent to {payment.counterparty}"


def _record_payment(
    owner: User, account: FinancialAccount, p: MpesaPayment
) -> tuple[Transaction, int]:
    rules = load_rules(owner)
    when = p.occurred_local.replace(tzinfo=MPESA_ZONE)
    main, created = _create_transaction(
        owner,
        account,
        posted_at=when,
        kind=TransactionKind.EXPENSE.value,
        amount_minor=-p.amount_minor,
        description=_describe(p),
        merchant_name=p.counterparty,
        provider_ref=p.code,
        rules=rules,
    )
    made = int(created)
    if p.cost_minor > 0:
        _, fee_created = _create_transaction(
            owner,
            account,
            posted_at=when,
            kind=TransactionKind.FEE.value,
            amount_minor=-p.cost_minor,
            description="M-PESA transaction cost",
            merchant_name="",
            provider_ref=f"{p.code}:fee",
            rules=rules,
        )
        made += int(fee_created)
    record_reported_balance_if_newer(account, p.balance_minor, when)
    return main, made


def _record_fuliza(
    owner: User, account: FinancialAccount, n: FulizaNotice, received_at: datetime
) -> tuple[Transaction | None, int]:
    # The draw is financing, not spending: the payment message already recorded the outflow.
    if n.fee_minor <= 0:
        return None, 0
    tx, created = _create_transaction(
        owner,
        account,
        posted_at=received_at,
        kind=TransactionKind.FEE.value,
        amount_minor=-n.fee_minor,
        description="Fuliza access fee",
        merchant_name="",
        provider_ref=f"{n.code}:fuliza_fee",
        rules=load_rules(owner),
    )
    return tx, int(created)


def _outcome_status(failure: ParseFailure) -> str:
    return (
        SmsParseStatus.UNPARSED if failure.reason in _UNPARSED_REASONS else SmsParseStatus.REJECTED
    )


def ingest_financial_sms(device: Device, records: Sequence[dict[str, Any]]) -> SmsBatchResult:
    settings_row: CollectionSettings = require_collecting(device)
    owner: User = device.owner
    result = SmsBatchResult(received=len(records))
    allowed = settings.PHONE_FINANCIAL_SENDERS

    seen: set[str] = set()
    fresh: list[dict[str, Any]] = []
    for item in records:
        if item["source_record_id"] in seen:
            result.duplicates += 1
            result.results.append(SmsOutcome(item["source_record_id"], "duplicate"))
        else:
            seen.add(item["source_record_id"])
            fresh.append(item)

    with transaction.atomic():
        known = set(
            FinancialSmsRecord.objects.filter(
                device=device, source_record_id__in=[r["source_record_id"] for r in fresh]
            ).values_list("source_record_id", flat=True)
        )
        account: FinancialAccount | None = None
        for item in fresh:
            sid = item["source_record_id"]
            if sid in known:
                result.duplicates += 1
                result.results.append(SmsOutcome(sid, "duplicate"))
                continue
            if not is_allowed_sender(item["sender"], allowed):
                # Not a financial sender: nothing is parsed and nothing is kept.
                result.rejected += 1
                result.results.append(SmsOutcome(sid, "rejected", reason="sender_not_allowed"))
                continue

            body = item["body"]
            body_hash = hashlib.sha256(normalize_body(body).encode("utf-8")).hexdigest()
            parsed = parse_mpesa(body)
            tx: Transaction | None = None
            template = ""
            reason = ""
            if isinstance(parsed, ParseFailure):
                status = _outcome_status(parsed)
                reason = parsed.reason
            else:
                status = SmsParseStatus.PARSED
                account = account or mpesa_account(owner)
                if isinstance(parsed, MpesaPayment):
                    template = parsed.template
                    tx, made = _record_payment(owner, account, parsed)
                else:
                    template = "fuliza"
                    tx, made = _record_fuliza(owner, account, parsed, item["received_at"])
                result.transactions_created += made

            try:
                with transaction.atomic():
                    FinancialSmsRecord.objects.create(
                        owner=owner,
                        device=device,
                        source_record_id=sid,
                        sender=item["sender"][:32],
                        received_at=item["received_at"],
                        parse_status=status,
                        template=template,
                        reason=reason,
                        body_sha256=body_hash,
                        transaction=tx,
                    )
            except IntegrityError:
                result.duplicates += 1
                result.results.append(SmsOutcome(sid, "duplicate"))
                continue
            if status == SmsParseStatus.PARSED:
                result.parsed += 1
            elif status == SmsParseStatus.UNPARSED:
                result.unparsed += 1
            else:
                result.rejected += 1
            result.results.append(SmsOutcome(sid, str(status), template, reason))

        touch_upload(settings_row)
    return result

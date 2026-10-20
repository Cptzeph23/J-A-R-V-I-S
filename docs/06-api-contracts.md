# 06 - API Contracts

Date: 2026-10-20 | Status: Draft for review

The machine-readable OpenAPI 3.1 file will live at `docs/api/openapi.yaml` and is generated and checked in CI starting Phase 1. This document fixes the surface and rules.

## 1. Global rules

| Topic | Rule |
|---|---|
| Base path | `/api/v1` |
| Auth | `Authorization: Bearer <access token>` except login, refresh, health, OAuth callback, provider webhooks |
| Content type | `application/json`; SSE for streams |
| Ownership | Every resource scoped to the authenticated owner; foreign IDs return 404 |
| Idempotency | `Idempotency-Key` header required on POST that creates or mutates (marked IDEM) |
| Pagination | Cursor: `?limit=<=100&cursor=<opaque>`; response `{results, next_cursor}` |
| Time | ISO 8601 UTC in payloads; dashboard accepts `tz` default from user profile |
| Money | `{ "amount_minor": 12500, "currency": "KES" }` |
| Rate limits | Auth and agent endpoints limited; 429 with `Retry-After` |
| Tracing | `X-Request-ID` returned on every response |

### Error envelope

```json
{ "error": { "code": "validation_error", "message": "Human readable", "details": {}, "request_id": "..." } }
```

| HTTP | code | Meaning |
|---|---|---|
| 400 | validation_error | Schema or business validation failed |
| 401 | unauthenticated | Missing/invalid/expired token |
| 403 | forbidden | Authenticated but not permitted (or policy denied) |
| 404 | not_found | Missing or not owned |
| 409 | conflict | Idempotency conflict, state conflict, duplicate |
| 422 | approval_required | Action needs approval; body includes approval id |
| 429 | rate_limited | Too many requests |
| 502 | provider_error | External provider failure |
| 503 | degraded | Dependency unavailable |

## 2. Endpoints

### Auth and devices

| Method | Path | Notes |
|---|---|---|
| POST | /auth/login | Body: email, password, device {platform, name}; returns access, refresh |
| POST | /auth/refresh | Rotating refresh; reuse revokes family |
| POST | /auth/logout | Revokes current device session |
| GET | /devices | List devices |
| PATCH | /devices/{id} | Update name, push token |
| DELETE | /devices/{id} | Revoke device and tokens |
| POST | /devices/{id}/push-token | Register FCM/APNs token |

### Conversations and agent

| Method | Path | Notes |
|---|---|---|
| GET/POST | /conversations | POST IDEM |
| GET | /conversations/{id}/messages | Paginated |
| POST | /conversations/{id}/messages | IDEM; starts agent run; returns message + workflow id |
| GET | /conversations/{id}/stream | SSE: tokens, step updates, approval prompts |
| GET | /workflows | Filter by state |
| GET | /workflows/{id} | Steps, invocations, status |
| POST | /workflows/{id}/cancel | Idempotent |
| GET | /approvals | Pending and history |
| GET | /approvals/{id} | Exact action, destination, data |
| POST | /approvals/{id}/decide | Body: decision, payload_sha256 echo, confirmation factor; voice-origin requests rejected for sensitive tools |

There is no public endpoint that invokes a tool directly.

### Integrations

| Method | Path | Notes |
|---|---|---|
| GET | /integrations | Status per integration |
| POST | /integrations/{provider}/connect | Returns authorization URL |
| GET | /integrations/{provider}/callback | State + PKCE validated |
| POST | /integrations/{id}/disconnect | Revokes at provider, deletes tokens |
| POST | /integrations/{id}/sync | Trigger incremental sync (rate limited) |
| POST | /webhooks/{provider} | Provider-authenticated; no bearer |

### Mail and calendar (client-facing reads; mutations go through agent/approval)

| Method | Path | Notes |
|---|---|---|
| GET | /mail/messages | Filters: unread, important, needs_reply, date range |
| GET | /mail/messages/{id} | Metadata + on-demand fetch flag |
| GET | /mail/summary | Daily/weekly summary |
| GET | /calendar/events | Range query |
| GET | /calendar/summary | Day/week |

### Finance

| Method | Path | Notes |
|---|---|---|
| GET/POST | /finance/accounts | POST IDEM |
| GET/POST | /finance/transactions | POST IDEM; filters: date, account, category, merchant, kind |
| PATCH/DELETE | /finance/transactions/{id} | Category edit, soft delete |
| POST | /finance/imports | Multipart CSV/XLSX; IDEM; returns batch id |
| GET | /finance/imports/{id} | Counts imported/duplicate/rejected with reasons |
| GET/POST | /finance/categories, /finance/merchants | |
| GET/POST/PATCH | /finance/budgets | |
| GET/POST/PATCH | /finance/savings-goals | |
| GET | /finance/reports/monthly?month=YYYY-MM | Full report object per SRS-FIN-05 |
| GET | /finance/recurring | Detected and confirmed |
| GET | /finance/recommendations | |
| POST | /finance/recommendations/{id}/decide | accept/reject |

### Phone activity (Android collector)

| Method | Path | Notes |
|---|---|---|
| POST | /phone/calls/batch | IDEM; up to N records; dedup by source_record_id |
| POST | /phone/messages/batch | IDEM; metadata only |
| POST | /phone/sms/financial/batch | IDEM; allowlisted senders; returns parse results |
| GET/PUT | /phone/collection-settings | Enabled, pause, retention |
| DELETE | /phone/records | Purge on request |

### Dashboard (read-only)

| Method | Path | Returns |
|---|---|---|
| GET | /dashboard/summary?range=today|week|month|custom&from&to&tz | All cards below with `data_as_of`, `source` |
| GET | /dashboard/spending | total, by_merchant[], by_category[], by_hour[], by_account[], top_transactions[] |
| GET | /dashboard/calls | received, missed, outgoing, rejected, total_duration_seconds, by_day[] |
| GET | /dashboard/messages | sent, received, by_day[] |
| GET | /dashboard/email | received, unread, unanswered, important, by_day[] |
| GET | /dashboard/activity | tasks by state, pending approvals, notifications by priority |

Example `/dashboard/spending` response:

```json
{
  "range": {"from": "2026-10-20T00:00:00+03:00", "to": "2026-10-20T23:59:59+03:00"},
  "currency": "KES",
  "total": {"amount_minor": 345000, "currency": "KES"},
  "by_merchant": [{"merchant": "Naivas", "total": {"amount_minor": 210000, "currency": "KES"}, "count": 2}],
  "by_category": [{"category": "Groceries", "total": {"amount_minor": 210000, "currency": "KES"}}],
  "excluded": {"transfers": 1, "unverified": 0},
  "data_as_of": "2026-10-20T12:10:03Z",
  "source": "transactions"
}
```

### Notifications and preferences

| Method | Path | Notes |
|---|---|---|
| GET | /notifications | Paginated |
| POST | /notifications/{id}/ack | Mark read |
| GET/PUT | /notification-preferences | Per category/integration, quiet hours |

### Memory and preferences

| Method | Path | Notes |
|---|---|---|
| GET | /memory | Inspect all stored items, with origin |
| POST | /memory | Add explicit item; IDEM |
| PATCH/DELETE | /memory/{id} | Correct or delete |
| GET | /preferences | Autonomy, approval, voice, retention settings |
| PUT | /preferences | Update |
| POST | /account/export | Starts export job |
| DELETE | /account/data | Deletion request (confirmation required) |

### System

| Method | Path | Notes |
|---|---|---|
| GET | /health/live | No auth; process up |
| GET | /health/ready | No auth; DB, Redis reachable |

## 3. Authorization matrix summary

| Resource | Rule |
|---|---|
| All owner data | owner_id == request.user.id |
| /webhooks/* | Provider signature or token verification, no bearer |
| /approvals/{id}/decide | Authenticated device session, non-voice channel for sensitive tools, hash must match |
| /phone/* | Device session with platform=android |
| Provider credentials | Never returned by any endpoint |

## 4. Contract testing

OpenAPI generated from DRF serializers (drf-spectacular, version verified in Phase 1), schema diff checked in CI, schemathesis-style contract tests against test settings.

# 02 - Technical Specification

Date: 2026-10-20 | Status: Draft for review

## 1. Technology stack

| Layer | Choice | Alternatives considered | Reason |
|---|---|---|---|
| Language/runtime | Python (version pinned in Phase 1) | Node.js/TypeScript | Existing Django experience |
| Backend | Django + Django REST Framework | FastAPI, NestJS | Admin, ORM, migrations, auth ecosystem, your experience |
| DB | PostgreSQL | SQLite (dev only), MySQL | Transactions, constraints, JSONB, partial indexes, full-text |
| Jobs | Celery + Redis | Dramatiq, RQ, django-q2, Postgres-backed queue | Mature scheduling (beat), retries, routing; Redis already needed for cache |
| Auth | djangorestframework-simplejwt (verify maintenance status in Phase 2) | Session cookies, custom opaque tokens | Mobile-friendly rotating refresh tokens |
| Streaming | Server-Sent Events | WebSocket | Simpler, one-directional token streaming, proxy-friendly |
| Mobile | Flutter | Native Kotlin/Swift, React Native | One codebase; native Android channel for call log/SMS and foreground service |
| Desktop | Responsive TS web app (React + Vite) then evaluate Tauri | Electron, Flutter desktop | Lightest to start; Tauri later for notifications/startup |
| LLM | Hosted API behind `LLMClient` interface | Local models | No device load; adapter keeps lock-in low |
| STT/TTS | Hosted first; on-device TTS fallback | On-device STT | Quality and battery |
| Crypto | `cryptography` (Fernet/AES-GCM envelope with key from env/secret manager) | DB-level pgcrypto | App-level control and key separation |
| Money | Integer minor units + ISO 4217 code, `decimal` only at the edge | float | Exactness |
| Lint/type/test | ruff, mypy (django-stubs), pytest, pytest-django, factory-boy, hypothesis | flake8, unittest | Speed and property testing for finance |
| Security scan | pip-audit, bandit | safety | CI-friendly |
| Observability | structlog JSON logs, Sentry-compatible error tracking, Prometheus metrics | OpenTelemetry later | Minimal for single user |
| CI | GitHub Actions | GitLab CI | Common default |

Version policy: no version is pinned from memory. Phase 1 task P1-01 queries PyPI and official release notes, records exact versions and compatibility (Django, DRF, Celery, psycopg, simplejwt) in `docs/VERSIONS.md`, and pins them in `requirements/`.

## 2. Conventions

- Layered per app: `models.py`, `services/` (writes, transactions), `selectors/` (reads), `api/` (serializers, views, urls), `tasks.py`, `tests/`.
- UUIDv4 primary keys; `created_at`, `updated_at` on all tables; every user-data table has `owner_id`.
- Owner scoping enforced by a base queryset manager and a DRF permission class; tests assert IDOR protection per endpoint.
- Settings split: `base`, `dev`, `test`, `prod`; secrets only from environment.
- API: `/api/v1`, JSON, cursor pagination, error envelope `{error:{code,message,details,request_id}}`.
- Mutating endpoints accept `Idempotency-Key`; stored in `idempotency_keys` with request hash and response.
- Time stored UTC; user timezone applied at presentation and for "day" boundaries in stats.

## 3. Agent design

- Custom orchestrator, no agent framework in v1 (ADR-004 rationale: the loop is small, state must be explicit and testable, frameworks add opaque state and dependency risk).
- `Tool` definition: name, purpose, pydantic input/output schemas, required permission, `mutating`, `approval` policy, timeout, retry policy, idempotency strategy, failure modes, audit events.
- Policy engine is pure Python and deterministic: `(user, tool, args, context) -> allow | require_approval | deny`.
- Workflow state machine: `created -> planning -> awaiting_clarification | awaiting_approval | running -> succeeded | failed | cancelled | needs_review`. Transitions are a table in code and unit-tested.
- Untrusted content is wrapped (`<untrusted source="email" id="...">`) and the system prompt states it is data only; this is defense in depth, not the control. The control is the policy layer.
- Model routing: small model for classification/extraction, larger model for planning and explanation. Token budget and timeout per workflow.
- Memory: PostgreSQL only in v1 (key-value preferences, approved facts, full-text search). Embeddings deferred until retrieval quality demonstrably needs them.

## 4. Finance design

- `Money(amount_minor:int, currency:str)`; exponent table per currency (KES 2).
- Transaction fingerprint = hash(owner, account, posted_date, amount_minor, normalized_description, provider_ref); unique per account, used for import dedup.
- `kind` enum: `income`, `expense`, `transfer_in`, `transfer_out`, `fee`, `refund`. Transfers between own accounts excluded from spend by default policy, stored in `accounting_policies`.
- Balances: `reported_balance` (from source, with timestamp) vs `computed_balance`; UI labels them separately.
- Reports are pure functions over a frozen transaction set and include period, currency, included accounts, excluded records, definitions, assumptions, reconciliation status, data freshness.

## 5. Phone activity and dashboard

- `phonelog` stores call and SMS metadata only (direction, timestamp, duration, counterpart, answered/missed). SMS bodies are not stored unless the sender is an allowlisted financial sender, and then only the parsed fields plus the raw text encrypted for audit.
- Dashboard endpoints are read-only aggregations computed by selectors with SQL group-by, cached briefly in Redis, in user timezone:
  - Spend today / range, by merchant, category, account, hour of day.
  - Calls received, missed, outgoing, duration totals.
  - SMS received/sent counts.
  - Emails received, unread, unanswered, important (from mailbox metadata).
  - Notification and task counts.

## 6. Performance budgets (to be verified by benchmark, not promised)

| Metric | Budget | Verification |
|---|---|---|
| p95 lightweight endpoint (excl. external calls) | < 500 ms | Locust/k6 on target host, Phase 14 |
| Dashboard summary p95 | < 800 ms on 12 months of data | seeded dataset test |
| Push enqueue-to-provider-accept | measured, target set after baseline | Phase 8 |
| Mobile idle background network | measured on device | Phase 11 |

## 7. Testing approach

pytest with deterministic fakes for LLM, Google, FCM, STT/TTS and social providers. Property-based tests for Money and report calculators. Prompt-injection corpus tests at the policy layer. Contract tests generated from the OpenAPI file. No live accounts in CI.

## 8. Environments

dev (local Postgres + Redis, no Docker required), test (CI services), prod (single host: gunicorn, Celery worker, beat, Postgres, Redis, reverse proxy with TLS). Docker introduced only if a phase shows a clear benefit.

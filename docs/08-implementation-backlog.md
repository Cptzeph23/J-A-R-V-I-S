# 08 - Implementation Backlog

Date: 2026-10-20 | Status: Draft for review

Rule: one phase at a time. Each phase ends with tests passing, lint/type checks clean, a status update, and your approval before the next phase starts. Effort is in engineering days for one engineer working with AI assistance; assumptions: familiarity with Django, no provider approval delays, no rework from review. These are estimates, not commitments.

Mapping to master-prompt milestones: M1 = Phase 0, M2 = P1, M3 = P2 + P6, M4 = P5, M5 = P6-P7, M6 = P3, P4, P9, M7 = P8, M8 = P11, M9 = P10, M10 = P13, M11-M13 = P14.

## Phase 0 - Planning and scaffold (this delivery)

Deliverables: docs 00-08, ADRs, scaffold script. Done when you approve the architecture and the directory tree is created.

## Phase 1 - Backend foundation (4 d)

| ID | Task |
|---|---|
| P1-01 | Verify and record framework versions in docs/VERSIONS.md; pin requirements |
| P1-02 | Django project, split settings, env loading, no secrets in repo |
| P1-03 | `common`: Money type, error envelope, pagination, request ID middleware, structlog setup |
| P1-04 | PostgreSQL and Redis config; Celery app + beat wiring; health endpoints |
| P1-05 | CI: ruff, mypy, pytest, pip-audit, bandit, makemigrations --check |
| P1-06 | OpenAPI generation; docs/ACCEPTANCE_MATRIX.md; PROJECT_STATUS.md |

Acceptance: `pytest` green; `/health/live` and `/health/ready` tested; Money property tests pass; CI config runs locally.

## Phase 2 - Auth, devices, audit (4 d)

Users model (UUID), login/refresh/logout with rotation and reuse detection, device registry and revocation, rate limiting, audit app (append-only), owner-scoped base manager and permission class, TOTP hook (optional).
Acceptance: SRS-SEC-01, 03, 04, 05 tests; two-owner IDOR test harness reusable by later phases.

## Phase 3 - Finance core (7 d)

Models, accounts, categories, merchants, manual entry, CSV importer with fingerprint dedup and per-row validation, rules-based categorization, accounting policy (transfers), monthly report calculators, import batch reporting.
Acceptance: SRS-FIN-01 to 05; scenarios 8, 9, 17 from master prompt; hypothesis tests on calculators.

## Phase 4 - Phone activity ingest and dashboard API (6 d)

phonelog models, batch upload endpoints, financial SMS allowlist parser (M-Pesa templates gathered from your real messages with numbers redacted), collection settings and purge, dashboard selectors and endpoints (spending, calls, messages, email stub, activity), timezone-correct day boundaries, short Redis cache.
Acceptance: SRS-PHN-01/02, SRS-DSH-01/02/03; dashboard totals equal fixture ground truth; query count and latency checked on seeded 12-month dataset.

## Phase 5 - Agent core (8 d)

Tool registry and contracts, policy engine, approvals with payload hash, workflow state machine, orchestrator with `LLMClient` interface and deterministic fake, SSE streaming, cost and token budgets, prompt-injection corpus tests, read-only finance and dashboard tools first.
Acceptance: SRS-AGT-01 to 07; scenarios 6, 7, 11, 15; no mutating tool executes without approval.

## Phase 6 - Integrations framework, Google OAuth, Gmail read (7 d)

OAuth with state and PKCE, encrypted credentials, revoke flow, Gmail incremental sync, mail metadata and classification, mail tools (read-only). Official Google docs checked for scopes, quotas, watch/push (Pub/Sub) vs polling.
Acceptance: scenarios 3, 10; sync resumes from cursor; no tokens in logs.

## Phase 7 - Calendar (5 d)

Calendar sync, summaries, conflict detection, create/update/cancel tools with approval and idempotency, timezone and recurrence tests.
Acceptance: scenario 12; no duplicate event on retry.

## Phase 8 - Events and notifications (6 d)

Event ingestion with dedup, importance rules, notification planner, quiet hours, grouping, FCM delivery with retry and tracking, reconciliation job; APNs adapter interface (live APNs when iOS app exists).
Acceptance: scenarios 1 (server side), 4, 5, 13, 18.

## Phase 9 - Advisor (5 d)

Recurring detection, unusual spend, budget tracking, recommendation objects with required fields, scenario model, accept/reject tracking.
Acceptance: every recommendation contains data, assumptions, impact, trade-offs, uncertainty, tracking method.

## Phase 10 - Web client (8 d)

TypeScript web app: login, chat with SSE, approvals UI, dashboard (spending, calls, messages, email cards), integrations, notification settings, privacy controls.
Acceptance: component and end-to-end tests with mocked API; dashboard numbers match API.

## Phase 11 - Flutter app and Android collector (10 d)

Auth, chat, dashboard, push, settings; Android native channel for call log and SMS metadata with visible consent, pause, purge; battery and network measurement.
Acceptance: scenario 2, 14 (client side); measured idle battery and network recorded.

## Phase 12 - Voice (6 d)

Push-to-talk STT, streamed TTS, interrupt, text-only mode, voice cannot approve.
Acceptance: SRS voice rules; network failure recovery test.

## Phase 13 - Social media, one platform (6 d)

Official API review, then connect, draft, approve, publish, analytics for one platform.
Acceptance: scenario 16; publishing only after approval; idempotent publish.

## Phase 14 - Hardening, performance, deployment (8 d)

Threat-model verification, load tests against budgets, backup and restore drill, monitoring, deployment scripts, rollback, release thresholds finalized and passed.
Acceptance: all 18 scenarios pass; thresholds met; no P0 defects open.

Total estimate: about 100 engineering days. Treat as a planning figure only.

## Standing definition of done (every phase)

1. Code complete and reviewed against the phase spec.
2. Tests added and passing; coverage report generated.
3. ruff and mypy clean; pip-audit and bandit run.
4. Migrations documented.
5. PROJECT_STATUS.md updated (done, open issues, decisions, next action).
6. No claim of success without command output to back it.

# 04 - Software Requirements Specification

Date: 2026-10-20 | Status: Draft for review

Requirement IDs refer to `03-PRD.md`. Each SRS item has a verification method (V): U unit, I integration, C contract, S security, P performance, M manual/device.

## 1. System behavior

1. Backend is the always-on authority. Clients display and request; they do not hold provider credentials.
2. Every agent action has one of the states `proposed`, `awaiting_approval`, `running`, `succeeded`, `failed`, `cancelled`, `needs_review`. Responses never describe a non-`succeeded` action as done.
3. Read-only tools may run automatically if permitted. Mutating tools require approval unless the owner has explicitly enabled autonomy for that tool, and money-moving tools do not exist in v1.

## 2. External interfaces

| Interface | Protocol | Notes |
|---|---|---|
| Client API | HTTPS JSON REST under /api/v1, SSE for streams | Token auth |
| Google | OAuth 2.0 + Gmail and Calendar REST | Scopes finalized in Phase 6 from official docs |
| LLM | HTTPS via `LLMClient` adapter | Timeouts, budgets |
| FCM / APNs | Provider HTTP APIs | Token registration per device |
| STT/TTS | Provider HTTP/streaming | Phase 12 |
| Android collector | Authenticated batch upload endpoint | Phase 11 |

## 3. Functional specifications

| ID | Specification | PRD ref | V |
|---|---|---|---|
| SRS-AGT-01 | Tool call arguments are validated against the tool's input schema before policy evaluation; invalid calls are rejected and logged | FR-AGT-002 | U |
| SRS-AGT-02 | Policy decision is a pure function of user, tool, args, context; LLM text cannot change it | FR-AGT-003 | U,S |
| SRS-AGT-03 | Approval stores the exact action payload and its SHA-256; execution recomputes and compares the hash | FR-AGT-003 | U,S |
| SRS-AGT-04 | Approvals expire (default 15 min) and are single-use | FR-AGT-003 | U |
| SRS-AGT-05 | Mutating tool execution is keyed by `(owner, tool, idempotency_key)` with a unique constraint | FR-AGT-005 | I |
| SRS-AGT-06 | State transitions outside the transition table raise and are logged | FR-AGT-004 | U |
| SRS-AGT-07 | Cancelling a workflow stops further steps; an in-flight external call is not assumed cancelled and its outcome is recorded | FR-AGT-004 | I |
| SRS-FIN-01 | Money is stored as integer minor units plus currency; no float anywhere in finance code | FR-FIN-004 | U |
| SRS-FIN-02 | Import computes fingerprints; duplicates within a batch or against existing rows are skipped and reported | FR-FIN-002 | U,I |
| SRS-FIN-03 | Invalid rows (bad amount, bad date, unknown currency) are rejected per row with a reason; valid rows still import only if the batch policy is `partial`, else the batch fails atomically | FR-FIN-002 | U |
| SRS-FIN-04 | Transfers between own accounts are excluded from expense totals under the default policy | FR-FIN-006 | U |
| SRS-FIN-05 | Report object includes period, currency, accounts, included count, excluded records with reasons, definitions, assumptions, reconciliation status, data_as_of | FR-FIN-011 | U |
| SRS-FIN-06 | M-Pesa SMS parser accepts only allowlisted sender IDs and known message templates; unparseable messages are stored as `unparsed` and counted | FR-FIN-003 | U |
| SRS-PHN-01 | Collector upload batches are idempotent via `(device, source_record_id)` unique constraint | FR-PHN-001 | I |
| SRS-PHN-02 | Pausing collection in app sets a server flag; server rejects uploads while paused | FR-PHN-003 | I |
| SRS-DSH-01 | Dashboard daily boundaries use the owner's timezone | FR-DSH-002 | U |
| SRS-DSH-02 | Spend-by-merchant and spend-by-category sums equal the day total; any difference is shown as `uncategorized` | FR-DSH-001 | U |
| SRS-DSH-03 | Every dashboard card returns `data_as_of` and `source` | FR-DSH-007 | C |
| SRS-EVT-01 | Webhook/event endpoints verify authenticity per provider before enqueue | FR-EVT-001 | S |
| SRS-EVT-02 | Events deduplicate on `(provider, external_event_id)` | FR-EVT-002 | I |
| SRS-EVT-03 | Reconciliation compares provider cursor to local state on a schedule and enqueues missing items | FR-EVT-002 | I |
| SRS-NTF-01 | Notifications obey quiet hours except for priority `critical` explicitly allowed by the owner | FR-EVT-004 | U |
| SRS-NTF-02 | Invalid push token response disables the token | FR-EVT-005 | I |
| SRS-SEC-01 | Refresh tokens rotate; reuse revokes the token family | FR-SEC-001 | U,S |
| SRS-SEC-02 | OAuth tokens encrypted with a key not stored in the database | NFR-SEC-001 | U,S |
| SRS-SEC-03 | Every query on user-owned data is owner-scoped; cross-owner access returns 404 | NFR-SCL-001 | I,S |
| SRS-SEC-04 | Audit log is append-only (no update/delete via application role) | FR-SEC-002 | S |
| SRS-SEC-05 | Rate limiting on auth and agent endpoints | NFR-SEC-002 | I |

## 4. Performance requirements

NFR-PERF-001 measured with a defined workload (to be written in Phase 14: dataset size, concurrency, hardware). No latency claim is made until measured.

## 5. Data requirements

See `05-database-schema.md`. Retention defaults: phone log detail 90 days, aggregated counts indefinite; raw SMS of financial senders encrypted, 12 months; audit log 24 months; LLM prompt logs not stored by default, only metadata (tokens, cost, latency).

## 6. Failure behavior

| Failure | Expected behavior |
|---|---|
| LLM malformed output | Schema validation fails; one repair retry; then workflow `failed` with reason shown |
| LLM timeout | Cancel, mark `failed`, no tool executed |
| Provider outage | Backoff with jitter, circuit breaker; user sees degraded status for that integration |
| OAuth revoked | Integration set `revoked`, sync disabled, user notified |
| Worker crash mid-workflow | Lease expiry, recovery per architecture 6.8 |
| Push provider down | Retry with backoff; delivery status visible |
| Duplicate webhook | Dropped by unique constraint, counted |
| Missing finance data | Report flags missing records; no estimate presented as fact |

## 7. Constraints

Single-user first; no Docker/Kubernetes/microservices without demonstrated need; official provider APIs only; no secrets in the repository.

## 8. Acceptance tests

Defined per phase in `08-implementation-backlog.md`. The 18 scenarios in the master prompt section 11 are mapped to phases there and tracked in `docs/ACCEPTANCE_MATRIX.md` (created in Phase 1).

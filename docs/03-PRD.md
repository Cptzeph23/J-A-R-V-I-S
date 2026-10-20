# 03 - Product Requirements Document

Date: 2026-10-20 | Status: Draft for review

## 1. Problem statement

Personal finances, email, calendar, phone activity and business social accounts are spread across apps. Nothing consolidates them, watches them continuously, or acts on the owner's behalf under controlled permissions.

## 2. Vision

One assistant that knows what happened, tells you what matters, shows the numbers, and does approved work, while the owner stays in control of every sensitive action.

## 3. Personas

| Persona | Description |
|---|---|
| P-Owner | Sole user (software engineer, Nairobi). Manages personal finances in KES, a Gmail account, a calendar, an Android phone, and possibly a business social page. Wants control, auditability, low battery impact. |
| P-Future | Possible additional users later. Drives multi-user-ready data isolation only. |

## 4. Priorities

P0 = MVP-blocking, P1 = needed soon after MVP, P2 = later, P3 = optional.

## 5. Functional requirements

| ID | Requirement | Pri | Module |
|---|---|---|---|
| FR-AGT-001 | Interpret text commands into intents and parameters | P0 | A |
| FR-AGT-002 | Execute tools only via a registry with schemas | P0 | A |
| FR-AGT-003 | Policy engine decides allow / approve / deny in backend code | P0 | A |
| FR-AGT-004 | Persist workflow state; show status; support cancel | P0 | A |
| FR-AGT-005 | Idempotent mutating tools; no duplicate execution | P0 | A |
| FR-AGT-006 | Ask clarification on ambiguous input | P1 | A |
| FR-AGT-007 | Report real status: proposed / pending / executed / failed | P0 | A |
| FR-AGT-008 | Safe recovery of interrupted workflows | P1 | A |
| FR-EML-001 | Connect Gmail via OAuth with minimum scopes | P0 | B |
| FR-EML-002 | Incremental sync, pagination, dedup | P0 | B |
| FR-EML-003 | Classify urgent/important, invoices, deadlines, meetings | P1 | B |
| FR-EML-004 | Summaries (daily/weekly) | P1 | B |
| FR-EML-005 | Draft replies; send only after approval | P1 | B |
| FR-EML-006 | Treat email as untrusted; injection-safe | P0 | B |
| FR-CAL-001 | Read events, daily/weekly summary | P0 | C |
| FR-CAL-002 | Create/update/cancel events with approval and idempotency | P1 | C |
| FR-CAL-003 | Conflict detection, time suggestions, timezone and recurrence support | P1 | C |
| FR-FIN-001 | Manual transaction entry | P0 | D |
| FR-FIN-002 | CSV import with dedup and validation | P0 | D |
| FR-FIN-003 | M-Pesa SMS parsing from Android collector (allowlisted senders) | P0 | D/K |
| FR-FIN-004 | Exact money handling, KES first, multi-currency capable | P0 | D |
| FR-FIN-005 | Categorization (rules; LLM suggestions flagged unconfirmed) | P0 | D |
| FR-FIN-006 | Monthly income, expense, net cash flow, savings rate | P0 | D |
| FR-FIN-007 | Budgets and tracking | P1 | D |
| FR-FIN-008 | Recurring/subscription detection | P1 | D |
| FR-FIN-009 | Unusual spending detection | P2 | D |
| FR-FIN-010 | Receipt extraction | P2 | D |
| FR-FIN-011 | Reports carry period, accounts, exclusions, definitions, assumptions, freshness | P0 | D |
| FR-FIN-012 | No money-moving actions in v1 | P0 | D |
| FR-SAV-001 | Recommendations with data, assumptions, impact, trade-offs, tracking | P1 | E |
| FR-SAV-002 | What-if scenarios | P2 | E |
| FR-SAV-003 | Accept/reject tracking | P2 | E |
| FR-VOI-001 | Push-to-talk STT and spoken replies | P1 | F |
| FR-VOI-002 | Text-only mode; interrupt speech | P1 | F |
| FR-VOI-003 | Voice never approves sensitive actions | P0 | F |
| FR-VOI-004 | Wake word (foreground only) | P3 | F |
| FR-EVT-001 | Provider push/watch where available, polling fallback | P0 | G |
| FR-EVT-002 | Event dedup and reconciliation | P0 | G |
| FR-EVT-003 | Push notifications via FCM; APNs for iOS | P0 | G |
| FR-EVT-004 | Priorities, grouping, quiet hours, per-integration preferences | P1 | G |
| FR-EVT-005 | Delivery tracking and retry | P1 | G |
| FR-SOC-001 | One platform: connect, draft, approve, publish, analytics | P2 | H |
| FR-MEM-001 | Explicit memory with inspect/correct/delete and retention | P1 | I |
| FR-MEM-002 | Separate facts from model assumptions | P1 | I |
| FR-CLI-001 | Web client: chat, dashboard, integrations, settings | P0 | J |
| FR-CLI-002 | Flutter app: login, chat, dashboard, push, settings | P1 | J |
| FR-CLI-003 | Cross-device conversation sync | P1 | J |
| FR-CLI-004 | Tauri desktop wrapper | P3 | J |
| FR-PHN-001 | Android collector uploads call metadata (received, missed, outgoing, duration) | P0 | K |
| FR-PHN-002 | Android collector uploads SMS metadata (counts, direction) | P0 | K |
| FR-PHN-003 | Collector is opt-in, shows what is collected, can be paused and purged | P0 | K |
| FR-PHN-004 | iOS and desktop display phone stats but do not collect | P0 | K |
| FR-DSH-001 | Daily spend total, by merchant ("where"), by category ("what"), by hour | P0 | L |
| FR-DSH-002 | Range filters (day, week, month, custom) in user timezone | P0 | L |
| FR-DSH-003 | Calls received, missed, outgoing, total duration | P0 | L |
| FR-DSH-004 | Messages sent/received counts | P0 | L |
| FR-DSH-005 | Emails received, unread, unanswered, important | P0 | L |
| FR-DSH-006 | Cards for tasks, approvals pending, notifications | P1 | L |
| FR-DSH-007 | Each card states data freshness and source | P0 | L |
| FR-SEC-001 | Single-user auth with device sessions and revocation | P0 | X |
| FR-SEC-002 | Audit log for every tool execution, approval, login, integration change | P0 | X |
| FR-SEC-003 | Data export and deletion | P1 | X |

## 6. Non-functional requirements

| ID | Requirement | Pri |
|---|---|---|
| NFR-PERF-001 | p95 < 500 ms for lightweight endpoints under defined workload, excluding external calls | P0 |
| NFR-PERF-002 | AI calls have timeouts, cancellation, bounded concurrency | P0 |
| NFR-REL-001 | Retries with backoff and jitter; idempotency on mutating actions | P0 |
| NFR-REL-002 | Backups with tested restore | P1 |
| NFR-SEC-001 | Encryption in transit and at rest; tokens encrypted at field level | P0 |
| NFR-SEC-002 | OWASP API, LLM and mobile checks in CI/review | P0 |
| NFR-SEC-003 | Dependency scanning in CI | P0 |
| NFR-RES-001 | No on-device model; idle background usage measured | P0 |
| NFR-SCL-001 | Owner-scoped data model; no schema assumption of single user | P0 |
| NFR-OBS-001 | Structured logs, request IDs, health checks, error tracking | P0 |
| NFR-PRV-001 | Retention settings for memory, phone logs, financial data | P1 |

## 7. User stories (selected)

| ID | Story | Acceptance |
|---|---|---|
| US-01 | As the owner, I see how much I spent today, where and on what | Dashboard shows total, per merchant, per category; totals equal sum of listed transactions; transfers excluded per policy |
| US-02 | I see calls received and missed today | Counts match uploaded call records for the local day |
| US-03 | I import a bank CSV without duplicates | Re-importing the same file adds zero rows |
| US-04 | I ask the assistant to send a reply | I see exact recipient and body; nothing sends until I approve on a non-voice channel |
| US-05 | I am told about an important email while the app is closed | Push delivered or delivery failure recorded and visible |
| US-06 | I revoke Google access | Tokens deleted; sync stops; audit entry written |
| US-07 | I pause phone collection | No new rows ingested; existing rows purgeable |

## 8. MVP scope

P0 items above across Phases 1-10 of the backlog (backend, auth, finance, dashboard, agent core, Gmail/Calendar read, push, web client).

## 9. Future features

Voice, email send, calendar write, advisor depth, social publishing, Flutter polish, Tauri, wake word, receipt OCR, additional providers.

## 10. Explicit exclusions

Money movement, autonomous publishing, autonomous email sending, unofficial or undocumented provider endpoints, scraping, on-device LLM, iOS call/SMS collection, multi-user UI.

## 11. Risks and assumptions

See `00-discovery.md` sections 3 and 6.

## 12. Success metrics (release thresholds to be fixed before Phase 14)

| Metric | Target |
|---|---|
| Duplicate side-effect rate | 0 in test suite and soak test |
| Unauthorized mutating tool executions | 0 in authorization and injection tests |
| Dashboard totals vs ground-truth fixtures | 100% match |
| Import dedup correctness on fixtures | 100% |
| API p95 | < 500 ms (NFR-PERF-001) |
| Cost per agent task | measured; cap set in Phase 5 |

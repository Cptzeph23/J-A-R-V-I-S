# 00 - Discovery Summary

Project: J.A.R.V.I.S (Personal AI Assistant)
Date: 2026-10-20
Status: Draft for review

## 1. Product understanding

A persistent, tool-enabled, event-driven personal agent. A Django backend runs 24/7, ingests events (email, calendar, finance, phone activity), executes approved actions through a policy-gated tool registry, and surfaces everything through a chat/voice client and a statistics dashboard on Android, iOS and desktop.

New requirement added in this brief: a **statistics dashboard** (spend per day by where/what, calls received/missed, messages, emails). This becomes Module K (Phone Activity) and Module L (Dashboard) in addition to Modules A-J.

## 2. Confirmed requirements (from the master prompt)

- Single-user deployment first, multi-user-ready data model (every row owner-scoped).
- Backend: Python/Django REST Framework, PostgreSQL, Celery, Redis.
- Hosted LLM; no local model on devices.
- KES first, multi-currency capable; exact money arithmetic; deterministic finance calculations.
- Financial integrations read-only initially.
- Human approval for sensitive actions; permissions enforced in backend code, never by prompt.
- Email/calendar via official APIs; OAuth 2.0 with least privilege.
- FCM + APNs push; provider events preferred over polling; reconciliation job for missed events.
- Publishing to social media requires approval initially; one platform first.
- Threat model documented before implementation.
- Phased delivery, tests per phase, no phase starts before the previous one is accepted.

## 3. Assumptions (provisional, not confirmed)

| ID | Assumption | Impact if wrong |
|---|---|---|
| A1 | Email and calendar provider is Google (Gmail API, Google Calendar API) | Adds a Microsoft Graph provider adapter; core unchanged |
| A2 | Android build is sideloaded or internal-track for personal use | Play Store distribution would restrict call log and SMS access (see section 5) |
| A3 | M-Pesa records come from SMS parsing on Android plus statement CSV import | If an official personal-account API exists and is usable, add as a provider |
| A4 | First social platform is decided at Phase 13 after API review | No impact on earlier phases |
| A5 | Desktop client is a responsive TypeScript web app first; Tauri evaluated later | Tauri wrapper adds native notification and startup behavior |
| A6 | Hosting is a single VPS or managed container host; no Docker or Kubernetes initially | Deployment doc changes only |
| A7 | LLM provider is Anthropic API behind an adapter interface | Adapter swap; no domain change |
| A8 | Single timezone default Africa/Nairobi, per-user configurable | None |

## 4. Technical constraints

- Backend must stay available when no client is open (push + server-side sync).
- No continuous microphone assumption on any platform.
- LLM output is untrusted input to the policy layer.
- All third-party content (email, SMS, social messages) is untrusted data.

## 5. Platform limitations (verify against current official docs when each phase starts)

| Capability | Android | iOS | Desktop |
|---|---|---|---|
| Read call log | Possible with READ_CALL_LOG; Google Play restricts this permission to approved use cases | Not available to third-party apps | Not applicable |
| Read SMS | Possible with READ_SMS; Play restricts to default SMS handler and narrow exceptions | Not available to third-party apps | Not applicable |
| Background execution | Doze and app standby limit it; use FCM high-priority sparingly and WorkManager | Severely limited; rely on APNs | Fine while app runs |
| Always-on mic / wake word | Foreground service only, with visible notification | Not permitted in background | Possible while app runs |
| Push | FCM | APNs | OS notification APIs |

Consequences:
1. Phone activity statistics (calls, SMS) are **Android-only** via a companion collector that uploads to the backend. iOS and desktop show the same stats read from the backend but cannot collect them.
2. If the app is distributed through Google Play, call log and SMS access may be rejected. Sideloading avoids store review but is a personal-use route only.
3. Backend sync is the source of truth for email, calendar and finance; the phone is only a client plus the Android phone-activity collector.

## 6. Main risks

See `01-architecture.md` section 9 and `07-threat-model.md`.

## 7. Recommended MVP

1. Backend foundation, single-user auth, device sessions, audit log.
2. Finance core: manual entry, CSV import, M-Pesa SMS parser (Android upload), dedup, categorization, reports.
3. Dashboard statistics API (spend by day/merchant/category; calls, SMS, email counts).
4. Agent core with tool registry, policy engine, approvals, mock LLM in tests.
5. Gmail read + Calendar read, FCM push.
6. Web client with chat and dashboard.

Deferred: voice, email send, calendar write, savings advisor depth, social media, Flutter polish, Tauri.

## 8. Critical questions

| # | Question | Default if unanswered |
|---|---|---|
| Q1 | Google or Microsoft for email/calendar? | Google (A1) |
| Q2 | Is the Android app sideloaded (personal use) or Play Store? | Sideloaded (A2) |
| Q3 | Is Android-only phone activity collection acceptable (no iOS call/SMS stats)? | Yes |
| Q4 | What M-Pesa data do you have: SMS only, statement PDF/CSV, or a business paybill/till with Daraja access? | SMS + CSV (A3) |
| Q5 | Which hosting and monthly budget (hosting + LLM)? | Single VPS, budget to be set in Phase 1 |
| Q6 | Is TypeScript/React acceptable for the desktop web client? | Yes (A5) |
| Q7 | Which social platform and is it a business page you control? | Decide at Phase 13 |
| Q8 | Should call and SMS counterpart numbers be stored in full, hashed, or counts only? | Store number encrypted at rest, retention 90 days, counts kept indefinitely |
| Q9 | Preferred LLM provider and spending cap per month? | Anthropic API behind adapter |

## 9. Items I could not verify

- Current stable versions of Django, DRF, Celery, Flutter: searches in this session returned no confirmed version numbers. Phase 1 pins versions after checking PyPI and official release notes.
- Whether Safaricom offers any official API for a personal M-Pesa account's transaction history. I don't know. Daraja is documented as a business-oriented API; treat personal history access as unavailable until confirmed on the official developer portal.

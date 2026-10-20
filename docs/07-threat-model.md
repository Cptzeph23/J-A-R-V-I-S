# 07 - Security Threat Model

Date: 2026-10-20 | Status: Draft for review
Method: STRIDE per trust boundary, mapped to OWASP API Top 10, OWASP Top 10 for LLM Applications, and OWASP Mobile Top 10. Re-verify the current OWASP editions at Phase 14.

Scale: Impact and Likelihood = Low / Medium / High.

## 1. Assets

OAuth refresh tokens, financial records, email content, phone call/SMS metadata, push tokens, audit log, encryption keys, LLM API key.

## 2. Threats

| ID | Threat | Impact | Likelihood | Mitigations | Residual | Verification |
|---|---|---|---|---|---|---|
| T01 | Stolen user credentials | High | Medium | Argon2/PBKDF2 hashing, login rate limit and lockout, MFA hook (TOTP, Phase 2/14), device sessions, new-device audit entry | Medium | Auth tests, brute-force test |
| T02 | OAuth token leakage (DB dump, logs) | High | Low | Field-level encryption, key from env/secret manager not in DB, tokens never logged, log scrubber, least scopes, revoke flow | Low | Unit test no token in logs; DB-dump inspection test |
| T03 | Prompt injection via email, SMS, social message | High | High | Untrusted-data wrapping; policy engine independent of LLM; mutating tools require approval; no tool can grant permissions; outbound recipients/content shown in approval; tool arg allowlists | Medium | Injection corpus test suite asserting no unauthorized tool call |
| T04 | Unauthorized email sending | High | Medium | Send tool is mutating + approval required; approval binds exact recipients and body hash; voice cannot approve; autonomy off by default | Low | Policy and approval-hash tests |
| T05 | Unauthorized financial action | High | Low | No money-moving tools exist in v1; finance integrations read-only; adding such a tool requires new ADR and threat review | Low | Registry test asserts no financial mutating tool |
| T06 | Cross-user data exposure (IDOR) | High | Medium | Owner-scoped managers, permission class, UUID PKs, 404 on foreign IDs | Low | Two-owner authorization test per endpoint |
| T07 | Malicious attachments | Medium | Medium | Attachments not auto-opened; type and size allowlist; parsed in isolated worker with timeout; no execution | Medium | Fixture tests with crafted files |
| T08 | Compromised device | High | Medium | Short access token, refresh rotation, remote device revoke, push token invalidation, no provider credentials on device, secure storage (Keystore/Keychain) | Medium | Revoke-and-reuse test |
| T09 | Excessive API permissions | Medium | Medium | Minimum scopes per feature, incremental consent, scope diff shown at connect, quarterly scope review | Low | Scope manifest test |
| T10 | Data exfiltration through the agent (leaking data into outbound content or URLs) | High | Medium | Output validation, no auto-forward, outbound tools approval-gated, URL fetch tool absent in v1, prompt minimization and redaction before LLM call | Medium | Exfiltration test cases |
| T11 | Replay of requests or approvals | Medium | Medium | Idempotency keys, single-use approvals with expiry, nonce on approval payload, TLS | Low | Replay tests |
| T12 | Duplicate tool execution | Medium | High | Unique (owner, tool, idempotency_key), DB transaction around state change, recovery rules | Low | Retry/crash simulation tests |
| T13 | Malicious social media content (comments/DMs as injection or abuse) | Medium | Medium | Same untrusted-data path as T03; no auto-reply; publishing approval-gated | Medium | Injection corpus includes social samples |
| T14 | Forged financial SMS (spoofed sender or fake M-Pesa message) | Medium | Medium | Sender allowlist, strict templates, records marked `unverified` until matched against statement import, user-visible flag | Medium | Parser fuzz and spoof fixtures |
| T15 | Phone log privacy exposure | High | Low | Opt-in, pause and purge, encrypted counterparts, short retention, no SMS bodies except financial senders | Low | Retention purge test, encryption test |
| T16 | Webhook spoofing | Medium | Medium | Verify provider signature/token, reject unknown, rate limit | Low | Webhook auth tests |
| T17 | Dependency vulnerabilities / supply chain | Medium | Medium | pip-audit, bandit, pinned and hashed requirements, minimal deps | Medium | CI gates |
| T18 | Backup exposure | High | Low | Encrypted backups, separate key, restricted access, restore drill | Low | Restore test |
| T19 | LLM provider data exposure | Medium | Medium | Data minimization, no secrets in prompts, provider retention settings reviewed at Phase 5 | Medium | Prompt-content tests |
| T20 | Denial of service / cost abuse (runaway LLM loops) | Medium | Medium | Token and step budgets, per-workflow cost cap, rate limits, circuit breakers | Low | Budget exhaustion tests |
| T21 | Insecure logging of personal data | Medium | Medium | Structured logs with allowlisted fields, scrubber, retention | Low | Log content tests |

## 3. Security controls checklist (gates for Phase 14)

- Secrets only from environment or secret manager; none in repo (gitleaks in CI).
- TLS everywhere; HSTS; secure cookie/headers where web client applies.
- Input validation at serializer and tool schema levels; output validation before tool execution and before notification/outbound content.
- Audit log covers login, token refresh anomalies, integration connect/disconnect, tool invocation, approval decision, data export/delete.
- Dependency scan, static analysis, and migration check in CI.

## 4. Review triggers

New tool added, new OAuth scope, new provider, any mutating capability, any change to approval or policy code requires a threat-model update.

# ADR-016: Web client

Status: accepted (Phase 10)

## Decision
- React 19, React Router 7, Vite 7, TypeScript 5.9 strict. TypeScript 7 was not adopted because the lint and test tooling pinned here was not verified against it.
- API types are generated from `docs/api/openapi.yaml`; `npm run types:check` fails when they drift from the backend contract.
- Tokens: access token in memory only; refresh token in `sessionStorage` (cleared when the tab closes; survives reload); device id in `localStorage`. Refresh calls are single-flight (backend rotates refresh tokens and treats reuse as theft, ADR-011). Cookies are not used, so there is no CSRF surface.
- Live updates: `GET /agent/workflows/{id}/stream` (SSE, snapshot per change, closes after 55 s, heartbeat 15 s). The client reads it with `fetch` so it can send the bearer header, reconnects with backoff, and falls back to polling. No token-level streaming.
- Money is formatted from decimal strings with BigInt; floats are never used for amounts.
- A card that is unavailable shows its reason, never zero.
- Dashboard, approvals, and chat read the server's numbers; the client computes nothing financial.

## Consequences
- A refresh token in `sessionStorage` is readable by any script on the origin; the strict CSP (no inline, no third-party script), no `dangerouslySetInnerHTML`, and no third-party UI runtime dependencies are the mitigations. An XSS would still be serious.
- Budgets, recurring charges and recommendations (Phase 9) have an API but no web screens in this phase.

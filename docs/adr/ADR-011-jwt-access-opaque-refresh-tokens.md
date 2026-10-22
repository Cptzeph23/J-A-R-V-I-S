# ADR-011: Short-lived JWT access tokens (PyJWT) with opaque rotating refresh tokens
Date: 2026-10-22 | Status: Proposed | Supersedes the authentication row of docs/02-technical-spec.md (simplejwt)

Context: Single owner, many device sessions, immediate revocation required (threat T08), refresh-token theft must be detectable (SRS-SEC-01). simplejwt's blacklist does not model token families or reuse detection, and the schema already defines `refresh_tokens` with `family_id`.
Alternatives: (a) djangorestframework-simplejwt, (b) fully opaque access tokens stored in the database, (c) PyJWT access tokens plus own refresh-token table.
Decision: (c). Access token: HS256 JWT, 10 minute default lifetime, claims `iss aud sub did jti typ iat exp`, algorithm allow-list fixed in code, required claims enforced. Authentication additionally loads the device row on every request, so revoking a device takes effect on the next request rather than at token expiry. Refresh token: 384-bit random secret, only its SHA-256 stored, single-use, rotated within a family; reuse outside a 10 second leeway revokes the device session and is audited. Signing key may be set separately via `JWT_SIGNING_KEY` and is required to be at least 32 bytes in production.
Consequences:
- Two small dependencies (PyJWT, argon2-cffi) instead of one framework; token logic is about 100 lines and fully unit tested.
- One indexed database read per authenticated request. Acceptable for single-user scale; revisit if multi-user load demands caching.
- Clients must serialize refresh calls: two concurrent refreshes with the same token, outside the leeway, are treated as theft and end the session.
- HS256 means one shared secret signs and verifies; asymmetric signing is unnecessary until a second service validates tokens.
- Key rotation for `JWT_SIGNING_KEY` currently invalidates all access tokens (clients recover through refresh). Multi-key rotation is a Phase 14 item.

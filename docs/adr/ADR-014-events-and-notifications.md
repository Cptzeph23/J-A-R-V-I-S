# ADR-014: Events, notification planning and push delivery

Status: accepted (Phase 8)

## Decision
- All sources (mail, calendar, integrations, approvals) emit events into one table with a unique key per source event. A pure rules module assesses importance; a pure planner decides notify, hold or suppress from preferences, quiet hours and grouping.
- Push text is generic. Subjects, titles and amounts never leave the server in a push; the app fetches detail over the authenticated API.
- Delivery is at-least-once per device with exponential backoff (30 s doubling, cap 1 h, jitter, honouring Retry-After, 6 attempts), lease-based crash recovery, and `collapse_key` = notification id so a device shows one notification.
- Gmail push (Pub/Sub) is only a trigger to sync sooner; polling remains the source of truth (ADR-012), and a reconcile job covers missed pushes.
- APNs is an interface that raises `ProviderNotConfigured` until an iOS client exists.

## Consequences
- Exactly-once is not claimed; the collapse key and ack make duplicates invisible in practice.
- Pub/Sub OIDC verification and `users.watch` registration are deferred until hosting is chosen (Phase 14).

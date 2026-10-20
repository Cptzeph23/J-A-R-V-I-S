# ADR-007: Server-side monitoring with push notifications, not device polling
Date: 2026-10-20 | Status: Proposed

Context: Mobile OSes restrict background work; battery and bandwidth must be conserved.
Alternatives: Client polling; persistent socket from the device.
Decision: Backend watches providers (push/watch where offered, scheduled incremental sync otherwise) and sends FCM/APNs notifications. Clients fetch on open or push.
Consequences: Delivery timing is best-effort and is not promised as instant. Reconciliation job covers missed events.

# ADR-013: Calendar sync by window reconciliation; writes that never contact other people

Status: accepted (Phase 7)

## Context
Google Calendar offers incremental sync tokens, but they cannot be combined with a time window, they expire and force a full resync, and a repeating event arrives as one master plus exceptions that must be expanded locally. The owner needs summaries, conflicts and a handful of safe changes, not a mirror of the whole calendar history.

## Decision
- Sync lists the occurrences inside a rolling window (14 days back, 90 ahead) with `singleEvents=true`, so the provider expands repeats, applies exceptions and resolves time zones. The stored rows are made to match inside one transaction, so an interrupted run changes nothing and there is no cursor to lose. Rows older than the window are dropped. A truncated listing never deletes later rows.
- Writes: create uses an event id derived from the owner and the idempotency key (a repeat finds the first event). Update and cancel present the event version the owner approved and fail with `event_changed` if it moved. Every call sets `sendUpdates=none`. Events with guests, events organized by someone else, and all-day events are never changed from here.
- No calendar write endpoint exists in the API; the only writer is the agent tool set, behind approvals.

## Consequences
- Events further than 90 days ahead are not visible to summaries.
- A full window listing each time costs more than a sync token would; acceptable for one calendar polled every 5 minutes. Revisit with push (Phase 8) or if quota becomes a concern.
- Changing guests, adding invitations or accepting invitations are deliberately out of scope for v1.

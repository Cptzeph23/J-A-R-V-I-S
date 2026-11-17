# ADR-012: Gmail sync by polling first; push notifications later

Status: accepted (Phase 6)

## Context
Gmail can notify a server of mailbox changes through `users.watch` and a Cloud Pub/Sub topic. That needs a public HTTPS endpoint, a Pub/Sub topic and subscription with the right IAM, and a watch that must be renewed about weekly. The system is not deployed anywhere reachable yet (hosting is open question Q5), and the push path only matters for scenario 1 (alert quickly when important mail arrives), which is Phase 8.

## Decision
Phase 6 syncs by polling with Gmail's history cursor: a scheduled task every minute starts a sync for each integration whose `next_sync_after` has passed (every 5 minutes when healthy), plus a manual "sync now" limited to one a minute. Push is added in Phase 8 as a trigger for the same sync function, so nothing built here is thrown away.

## Consequences
- New mail is seen within about 5 minutes. Acceptable for statistics; Phase 8 shortens it for alerts.
- Each poll with nothing new costs one cheap `history.list` call.
- No public endpoint or cloud project beyond OAuth is needed to run Phase 6.
- Quota numbers were not verified against Google's current documentation in this phase (the quota page was not reachable). The design stays far below published per-user limits for one mailbox, and a rate-limit response from Google is honoured through `Retry-After`.

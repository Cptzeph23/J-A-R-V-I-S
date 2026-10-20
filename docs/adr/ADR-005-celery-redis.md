# ADR-005: Celery + Redis for background jobs
Date: 2026-10-20 | Status: Proposed

Context: Need retries, scheduling (sync, reconciliation, purge), routing, and bounded concurrency; Redis also serves cache and rate limits.
Alternatives: Dramatiq, RQ, django-q2, Postgres-backed queue.
Decision: Celery with Redis broker, Celery beat for schedules, acks-late and idempotent tasks.
Consequences: Mature but configuration-heavy. Redis is a required service. A Postgres-backed queue stays a fallback if operational burden outweighs benefit.

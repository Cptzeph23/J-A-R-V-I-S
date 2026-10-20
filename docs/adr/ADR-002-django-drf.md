# ADR-002: Django + DRF for the backend
Date: 2026-10-20 | Status: Proposed

Context: Needs ORM, migrations, admin, auth, and a large ecosystem; owner is experienced in Django.
Alternatives: FastAPI (async, lighter, more assembly), NestJS (TypeScript).
Decision: Django + DRF. SSE streaming served via an ASGI server; verify the streaming approach in Phase 5.
Consequences: Fast delivery and mature tooling. Async-heavy paths (streaming) need care; LLM and provider I/O run in Celery or async views. Versions pinned in Phase 1 after verification.

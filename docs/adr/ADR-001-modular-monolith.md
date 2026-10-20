# ADR-001: Modular monolith over microservices
Date: 2026-10-20 | Status: Proposed

Context: Single user, one engineer, many modules with clear boundaries.
Alternatives: (a) microservices per module, (b) modular monolith, (c) serverless functions.
Decision: Modular monolith with Django apps as module boundaries; Celery worker and beat run from the same codebase.
Consequences: One deploy, simple transactions and testing. Module boundaries enforced by convention (services/selectors, no cross-app model imports in views). Extraction later is possible along app lines if a measured need appears.

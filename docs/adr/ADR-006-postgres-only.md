# ADR-006: PostgreSQL as the only database; no vector store initially
Date: 2026-10-20 | Status: Proposed

Context: Relational finance data, JSONB payloads, full-text search for memory and mail metadata.
Alternatives: Add a vector DB; add a document store; SQLite.
Decision: PostgreSQL only. Memory retrieval uses key lookup and full-text search. pgvector or a vector DB is introduced only after evaluation shows keyword retrieval is inadequate.
Consequences: One datastore to operate and back up. Semantic retrieval is deferred.

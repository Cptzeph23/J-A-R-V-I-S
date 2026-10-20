# ADR-004: Hosted LLM behind an adapter; custom orchestrator, no agent framework
Date: 2026-10-20 | Status: Proposed

Context: Must not load models on phones; the agent loop needs explicit, testable state and policy enforcement.
Alternatives: Local models; agent frameworks (LangChain/LangGraph-style); custom loop.
Decision: Hosted LLM via `LLMClient` interface (provider swappable). Custom orchestrator with an explicit state machine, tool registry and policy engine.
Consequences: No framework dependency or hidden state; more code to write and test. Provider data-handling terms reviewed in Phase 5. Reconsider a framework only if workflow complexity demonstrably outgrows the custom loop.

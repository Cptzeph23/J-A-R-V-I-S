# ADR-003: Flutter for mobile
Date: 2026-10-20 | Status: Proposed

Context: Android and iOS needed; Android requires native call log/SMS access and a foreground service.
Alternatives: Native Kotlin + Swift, React Native.
Decision: Flutter with a thin native Android layer (platform channels) for the collector and push handling.
Consequences: One UI codebase. Native Android code is required for phone activity; iOS has no equivalent. Flutter version verified at Phase 11.

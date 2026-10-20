# ADR-010: Android-only phone activity collector for call and SMS statistics
Date: 2026-10-20 | Status: Proposed

Context: Dashboard must show calls received/missed and message counts; M-Pesa records arrive as SMS. iOS exposes neither call log nor SMS to third-party apps; Google Play restricts READ_CALL_LOG and READ_SMS.
Alternatives: (a) no phone stats, (b) carrier APIs (not known to exist for consumers), (c) Android collector.
Decision: Android collector in the Flutter app via native channel, opt-in, metadata only (financial-sender SMS parsed and raw text encrypted), batch upload, pause and purge controls. Assumes sideloaded/personal distribution.
Consequences: Phone stats unavailable from iOS; Play Store distribution may be blocked; privacy controls and retention are mandatory. Revisit if distribution plans change.

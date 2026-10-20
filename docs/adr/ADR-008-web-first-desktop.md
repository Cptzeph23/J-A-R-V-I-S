# ADR-008: Responsive web app first for desktop; evaluate Tauri later
Date: 2026-10-20 | Status: Proposed

Context: Desktop needs chat, dashboard, notifications; owner codes TypeScript.
Alternatives: Tauri, Electron, Flutter desktop.
Decision: TypeScript web app first (framework chosen in Phase 10). Tauri wrapper evaluated afterward for native notifications, startup behavior and optional local file access.
Consequences: Lowest initial cost. Startup-on-login and local file access wait for the wrapper.

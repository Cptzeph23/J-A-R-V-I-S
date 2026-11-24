# ADR-015: Deterministic advisor

Status: accepted (Phase 9)

## Decision
- All analysis (recurring charges, unusual spend, budgets, scenarios) is deterministic code over the owner's transactions, using the same inclusion rules as the monthly report. No language model computes or estimates a figure; it can only read and explain results through read-only tools.
- Unusual means different from the owner's own history, measured with median and median absolute deviation, so one odd month does not hide a real one. Nothing is flagged without three earlier months in that category, and the result lists categories skipped for short history.
- A recommendation is a stored object that must state data, assumptions, impact range, trade-offs, uncertainty and a tracking method. The engine drops any draft that fails the gate and the database independently requires the six keys.
- Accepting records a decision and starts a 30 day measurement window; the outcome (met, partial, not met, inconclusive) is computed from real transactions and says that it shows what happened, not proof of cause. The assistant cannot accept or reject.
- Rules are versioned (`rules_version`, calculator `DEFINITION_VERSION`) so a change in logic is visible in stored results.

## Consequences
- Thresholds (3 months of history, 3 and 4 spreads, 125% and 2x ratios, KES 500 minimum difference, 80% budget warning, day 7 projection) are conservative starting points to tune against real data.
- Purchase-level anomalies are shown in the API but do not create recommendations yet.

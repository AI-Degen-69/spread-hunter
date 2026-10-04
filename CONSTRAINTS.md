# Constraints & Quality Guardrails — Issue #361

Branch: `i361/dead-band-rehearsal` | Issue: #361

## Quality & Tests
- **Zero regressions**: Targeted suites `tests/test_trader_loop.py` and `tests/test_statistics_report.py` must pass cleanly.
- **Anti-cheat**: No skipped tests, no deleted assertions, no suppressions or linter silencing.
- **No new external dependencies**: Use Python standard library and existing project dependencies only.

## Safety & Venue Boundaries
- **No live trading**: Rehearsals must run in `--mode shadow` via `core_brain.shadow_run`. No private keys or signers loaded.
- **No production DB write**: `data/orders.db` must never be touched, modified, or targeted by `--db`. All runs must target dedicated scratch stores (e.g. `data/361_shadow_*.db`).
- **No default changes**: Shipped defaults in `core_brain/config.py` (specifically `requote_dead_band = 0.03`) must NOT be edited in this issue. Setting overrides occur solely via process environment variable `HUNTER_REQUOTE_DEAD_BAND`.
- **Honest reporting**: No fabricated run data. Metrics must be computed directly by `core_brain.statistics_report::write_statistics_report` and live telemetry. If sample sizes or fill rates are low/zero, the verdict must honestly reflect that (e.g. Inconclusive / Reject).

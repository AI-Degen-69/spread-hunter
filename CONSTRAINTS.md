# Constraints & Quality Guardrails — Issue #360

Branch: `i360/run-the-queue-hold-rehearsal` | Issue: #360

## Quality & Tests
- **Zero regressions**: Targeted suites `tests/test_trader_loop.py`, `tests/test_completable_pair_gate.py`, and `tests/test_statistics_report.py` must pass cleanly.
- **Anti-cheat**: No skipped tests, no deleted assertions, no suppressions or linter silencing.
- **No new external dependencies**: Use Python standard library and existing project dependencies only.

## Safety & Venue Boundaries
- **No live trading**: Rehearsals must run in `--mode shadow` via `core_brain.shadow_run`. No private keys or signers loaded.
- **No production DB write**: `data/orders.db` must never be touched, modified, or targeted by `--db`. All runs must target dedicated scratch stores (e.g. `data/scratch_qhold_*.db` or `runtime/scratch/*.db`).
- **No default changes**: Shipped defaults in `core_brain/config.py` (specifically `requote_hold_queue_shares = 0.0`) must NOT be edited in this issue. Setting overrides occur solely via process environment variable `HUNTER_REQUOTE_HOLD_QUEUE`.
- **Honest reporting**: No fabricated run data. Metrics must be computed directly by `core_brain.statistics_report::write_statistics_report` and `core_brain.kpi::report`. If sample sizes or fill rates are low/zero, the verdict must honestly reflect that (e.g. Inconclusive / Reject).

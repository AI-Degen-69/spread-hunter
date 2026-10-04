# Constraints & Quality Guardrails — Issue #370

Branch: `i370/velocity-filter-dynamic-offsets` | Issue: #370

## Quality & Tests
- **Zero regressions**: Targeted suites `tests/test_velocity_gate.py`, `tests/test_quotes.py`, `tests/test_market_feed.py`, and `tests/test_trader_loop.py` must pass cleanly.
- **Anti-cheat**: No skipped tests, no deleted assertions, no suppressions or linter silencing.
- **No new external dependencies**: Use Python standard library and existing project dependencies only.

## Safety & Invariants
- **Strict pair-cost invariant**: Combined maker buy price must always satisfy `price_UP + price_DOWN < 1.00`. Any dynamic offset resulting in combined cost >= $1.00 must be refused immediately (`dynamic_pair_sum`).
- **Fail-safe fallback**: When dynamic offset is disabled (`HUNTER_DYNAMIC_OFFSET=0`) or range data is missing/stale, quoting behavior must be byte-for-byte identical to the proven static offset baseline (`reward_offset`).
- **No production DB write**: `data/orders.db` must never be touched, modified, or targeted.
- **No live trading**: Rehearsals and tests must run with `--no-live` or in shadow mode with zero private keys.

# CONSTRAINTS.md — Issue #386 (quote in-play esports markets)

## Zero regressions
- `tests/test_pre_start_gate.py`, `tests/test_trader_loop.py`, `tests/test_market_quote.py`,
  `tests/test_completable_pair_gate.py` must pass after every change (focused suites only;
  the full `pytest -q` suite is the GitHub CI merge gate, not a local loop).
- New behavior needs tests that fail without the change (RED first, then GREEN).

## Anti-cheat
- No skipping, disabling, or weakening existing tests or assertions.
- No linter suppression, no `type: ignore`, nosilent `except: pass` around new parsing.
- NoThreshold edits to make red green (caps, spreads, depths stay as configured).

## Boundaries
- No new dependencies. No config value changes (`config.py` untouched, incl. `min_t_remaining_sec`).
- `data/orders.db` is production: read-only, never rewritten.
- `LiveMarket.t_remaining()` and `end_ts` keep real-expiry meaning for all other consumers
  (aged-out rescue, order-manager window loop, markouts, KPI).
- Selection/ranking (`scripts/filter_markets.py`, `scoring/`) untouched.
- Only `evaluate_market_quote` switches clocks; `decide_quotes` and everything below it unchanged.

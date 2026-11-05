# CONSTRAINTS - #942

Branch: i942/size-order-positions-with-the-kelly-criterion | Issue: #942

## Zero regressions

- Targeted suites green during development and after review fixes:
  `tests/test_risk.py`, `tests/test_quotes.py`, `tests/test_dynamic_risk_caps.py`,
  `tests/test_config_single_source.py`.
- Full `python -m pytest -q` is the GitHub CI merge gate (Ubuntu + Windows), not a local
  gate. The repo does not repeat it locally.
- Every changed behaviour needs a test that fails without the change.
- On a live tournament, `tests/conftest.py`'s `block_real_state_writes` fails teardown
  with `db-wal` errors — environmental, not a failure; never "fix" the guard.

## Hard boundaries

- Kelly allocates INSIDE the existing caps, never past them:
  `bot_risk_exposure_max_naked_usd`, `bot_risk_exposure_max_order_usd`,
  `bot_risk_exposure_max_total_usd`, `bot_pos_price_max_pair_cost_usd`,
  `bot_quote_size_min_shares`, `bot_quote_size_max_shares`, the (1−u)² taper and the
  `(budget − naked)/price` remainder cap are unchanged in shape and order.
- `risk.size_for` stays I/O-free. Measured stats arrive via cfg merged per cycle
  (`_fleet_state` pattern), never via a DB read inside `size_for`.
- VENUE_SYNC_RUN_ID closes are excluded from the Kelly sample, mirroring the KPI slice.
- Mode not selected (`bot_quote_size_mode != "kelly"`) ⇒ byte-identical behaviour; the
  fleet-state Kelly branch reads zero closes and merges zero new keys.
- `bot_quote_size_mode` shipped default stays `"dollars"`. Kelly is opt-in.

## Anti-cheat

- No skipping/deleting/weakening tests or assertions; no lint suppression to get green.
- Kelly math is never reimplemented: call `compute_trade_analytics` (kpi.py) and read
  `n_closes` / `win_rate` / `payoff_ratio` / `kelly_fraction` / `half_kelly` from it.
- NULL stays NULL: an unmeasurable Kelly is never coerced to 0.0-as-"no edge";
  insufficient sample falls back to dollars mode byte-for-byte.

## Dependencies

- No new external dependencies. No schema change to `data/orders.db`. Production registry
  is read-only here.

## Deliberate, not a regression

- The research note lands BEFORE any sizing code (issue acceptance criterion) — the
  build order enforces it; do not "improve" the order.
- Fixed: nothing in this issue is red-at-merge by design.

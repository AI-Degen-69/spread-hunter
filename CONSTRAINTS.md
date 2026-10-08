# CONSTRAINTS — #419 (locked by Station II, enforced through Station V)

Supersedes the previous issue plan constraints for this branch only.

## Zero regressions

- Focused suites that must pass for the touched behavior:
  `tests/test_plan_orders_mid_hold.py` (new),
  `tests/test_plan_orders_asymmetric_hold.py`,
  `tests/test_trader_loop.py`.
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every behavior change needs a test that fails without the change (RED first, per `test-driven-development`).
- The shared `TestRefusedHold` books move only as far as the plan says
  (0.66/0.68) with every existing assertion in that class still holding;
  a separate in-band grace test proves the new hold.

## Cancel-path freeze

- Terminal refusals (named stops, hard stop), `lifecycle_cancel`, explicit
  `cancel_order_ids`, and the cancel-wins-over-replace rule: unchanged,
  in band or out.
- `token_mids=None` (and any caller that never passes it) keeps today's
  behavior exactly — the guard is additive only.
- Missing midpoint, one-sided book, crossed book: guard stands down.
- No duplicate submits for held tokens on either submit branch.
- `shadow_fills.py`, `config.py`, `quotes.py`, `live_fill_engine.py`,
  `markets.py`, `_market_cfg`: untouched.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration, no database rewriting; tests use temporary DBs.
- No live orders are opened or completed; no `quote`, `complete`, Trader
  loop, or dashboard START. Visit tests run `_visit_one` with stubs.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies without explicit operator approval.
- One named module constant (`MID_HOLD_BAND = 0.02`); no new config
  field, no new helper module.

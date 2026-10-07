# CONSTRAINTS — #411 (locked by Station II, enforced through Station V)

Supersedes the previous issue plan constraints; this issue is the money path and the pair-cost guardrail has to be explicit.

## Zero regressions

- Focused suites that must pass for the touched behavior:
  `tests/test_plan_orders_asymmetric_hold.py`,
  `tests/test_ladder_quotes.py`,
  `tests/test_completable_pair_gate.py`,
  `tests/test_trader_loop.py`,
  `tests/test_live_quotes.py`.
- Full-repo sweep stays with CI on push; locally run only the focused suites.
- Every behavior change needs a test that fails without the change (RED first, per `test-driven-development`).

## Money-lever freeze

- The hard pair ceiling is always on: the rounded pair cost must refuse any value above `$0.99`, regardless of config.
- Configured caps can only tighten the rule; they may never loosen it.
- Existing `hard_block` / `completable_pair_block` logic stays active; do not replace the risk gate with a single duplicate path.
- No live orders are opened or completed in this station; all verification is through tests, replay, or dry-run code paths.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration, no database rewriting, no order-registry change for this issue.
- The fix belongs in the quoting / planner gate layer, not in the registry or the execution path.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies without explicit operator approval.
- A pair over the cap is never treated as an acceptable “close enough” result.
- The helper must apply consistently in from-mid, ladder, and legacy quote generation.

## Performance

- No quote churn: the asymmetry must hold a downward-moving BUY within `requote_hold_below_target` while still ensuring the pair cannot exceed the cap.
- The logic must remain narrow: one hard helper, same risk gate semantics, and no extra per-cycle book fetches or network reads.

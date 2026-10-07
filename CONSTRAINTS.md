# CONSTRAINTS — #401 (locked by Station II, enforced through Station V)

## Zero regressions

- These suites must pass after every change touching their modules:
  `tests/test_recent_trades_taker_side.py`, `tests/test_shadow_run.py`,
  `tests/test_shadow_exec.py`, `tests/test_shadow_fills.py`,
  `tests/test_queue_marks.py`, `tests/test_queue_clear_gate.py`.
- Full-repo sweep stays with CI on push; locally run only the focused suites.
- Every new behavior needs a test that FAILS without the change (RED first,
  per `test-driven-development`).

## Contract freeze

- Keep the two-argument `traded_fn(condition_id, seen)` contract and the
  `{asset: {price: volume}}` return shape.
- Keep exact-price (4dp) matching in `credit_fills`; keep the shared tape map.
- `recent_sell_flow` out of scope — do not change it.
- `taker_side=None` behavior (whole tape for `scripts/book_tape_recorder.py`)
  must keep working.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- The incident store `data/06_shadow_prudent_07-10_13-20.db`: open read-only
  (stdlib sqlite3, `file:...?mode=ro`); never schema-init helpers (they write).
- Do NOT run shadow rehearsals, Trader loop, `quote`, or `complete` to
  investigate — rehearsals write state; opening commands spend money.
- Diagnosis reads the public `/trades` endpoint only. No signer, no orders.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no linter suppression.
- No new external dependencies without explicit operator approval.
- A missing-side row stays skipped (under-counting is the conservative
  direction for a fill model) — do not "repair" it into volume.

## Performance

- Pagination is bounded (page cap; offsets inside the documented 10,000 cap).
- Bootstrap with empty `seen`: read one page, mark it seen, credit nothing.

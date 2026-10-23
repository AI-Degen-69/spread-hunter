# CONSTRAINTS.md — Issue #795: hard-stopped pair resells its own closed shares

Branch: `i795/fix-a-hard-stopped-pair-keeps-reselling-its-own-cl` | Issue: #795

Locked during Station II planning. Stations III–V must hold every line below.
Tighten only with operator approval; never loosen silently.

## Zero regressions

- Focused suites for the touched modules must be green after the change:
  `tests/test_single_buy_saver.py tests/test_single_leg_lifecycle.py`
  `tests/test_refill_after_exit.py tests/test_partial_exit_stays_managed.py`
  `tests/test_hard_stop_reset.py tests/test_order_registry.py tests/test_rc_fixes.py`.
- Existing behaviors stay pinned with unchanged outcomes: the 5-share
  shortfall refusal in `test_refill_after_exit.py`, the `[10, 10]` refill
  sales, the partial-exit replay, the one-connection test
  (`tests/test_single_buy_saver.py:458-481`), and the `6.0` expectation in
  `tests/test_rc_fixes.py:151-177`.
- Every new behavior needs a test that fails without it: the moe two-pass
  replay (old pair reads zero naked, new close books new-pair cost), the
  close cost cap (second close books 1.00 not 5.00), and the netting
  boundaries (inclusive earliest-`posted_ts`, both-prices-set exclusion,
  unmapped-token exclusion).

## Scope fences (hard)

- **Only these production files change:** `core_brain/order_registry.py`
  (one new pair-scoped rescue-totals read next to
  `get_pair_orders_with_fill_totals`) and `core_brain/single_buy_saver.py`
  (`load_pair` netting, removal of the two `_prior_exit_shares` credits,
  `cost_basis` cap in `_record_exit_close`, poll-loop review).
- `registry_naked_usd` and its `rescue_net` CTE stay byte-identical in
  behavior — the new read copies its predicates, never edits them.
  `get_pair_orders_with_fill_totals` keeps its signature and behavior for
  its other callers.
- **No writes to any named `.db`.** All new tests use temporary
  `OrderRegistry` databases. `data/orders.db` and
  `data/01_shadow_23-10_20-16.db` are read-only evidence, never fixtures.
- **No venue-reaching command.** No `quote`, `complete`, Trader loop, or
  dashboard START. Verification is `pytest` on the focused suites only.

## Anti-cheat

- Never skip, xfail, or delete a test to get green; never weaken an
  assertion or suppress a linter to pass.
- The netting and the removal of BOTH prior-exit credits land in the same
  change: `load_pair` netting with either credit left in place double-counts
  (credit hides real shortfalls) and is a red state, never a checkpoint.
- Never widen the time scope beyond `rescue_net`'s rule (closes at or after
  the pair's earliest `posted_ts`). The known concurrent-pairs-on-one-token
  limit stays documented, not fixed here.

## Performance / architecture ceilings

- The pair read stays at **one connection** (`tests/test_single_buy_saver.py:458-481`):
  fill rows + rescued totals in a single `_conn()` block, no per-order reads.
- No new external dependency, no new package, no new file under
  `core_brain/`. One registry method, netting inside the existing
  `load_pair` view — no second inventory abstraction.

## Safety (repo rules)

- `data/*.db` are gitignored and must never be committed.
- Retire `_prior_exit_shares` only when a grep proves zero remaining
  callers; its scoping tests move to `load_pair`, never dropped.

# tasks/plan.md — Issue #795

Branch: `i795/fix-a-hard-stopped-pair-keeps-reselling-its-own-cl` | Issue: #795

## CodeRabbit intake (read once, recorded here so Stations III–V never re-read it)

- **Adopted:** netting inside the shared `load_pair` view (single site, not
  poll-loop-only); pair-scoped registry read copying the `rescue_net`
  predicates; removal of BOTH `_prior_exit_shares` credits in the same change;
  `cost_basis = min(size × netted_avg, netted_notion)` cap; poll loop keeps
  its existing `naked <= SIZE_EPS` skip with no new branch; moe replay +
  boundary tests; keep `get_pair_orders_with_fill_totals` and
  `registry_naked_usd` unchanged.
- **Rejected (over-split/bloat):** nothing structural — CodeRabbit's 3-task
  shape (inventory view / remove credits + cap / tests) is kept as-is. The
  "extract shared CTE fragment" option was considered and rejected in favor of
  a copied rule with cross-reference comments + a parity test, so
  `registry_naked_usd` (risk-cap path) is never touched.
- **[UNVERIFIED] at plan time, verified during Station II:** every cited seam
  checked against the live tree — `load_pair` builds from fills only
  (`single_buy_saver.py:640-692`, `rows =
  registry.get_pair_orders_with_fill_totals(pair_id)`); credits at
  `_check_positions` (`:805`) and exit sizing (`:1142-1147`); uncapped
  `cost_basis = size * heavy_avg` in `_record_exit_close` (`:901-940`);
  poll-loop skip (`:2147-2150`) and `SingleLegPosition` from raw legs
  (`:2192-2209`); `rescue_net` CTE (`order_registry.py:2893-2929`);
  one-connection read (`order_registry.py:1543-1564`); `RESCUE_METHODS` in
  `close_methods.py:101`; all 7 focused test files exist.

## Spec (embedded — Standard tier, no SPEC.md ceremony)

**Goal:** a pair that already sold all its shares reads zero naked size and
zero cost, so the stop path never fires for it again and a later stop on the
same market books the new pair's own cost.

**Acceptance:**
1. After a full rescue exit, `load_pair(old_pair)["naked"] == 0` on both
   legs and the poll loop skips it (no sale, no lifecycle transition).
2. The moe replay books close #41 at cost 8.20 / pnl −6.10 and close #43 at
   cost 1.50 / proceeds 1.29 / pnl −0.21 — never 8.20 on the second close.
3. One close never removes more cost than the sold shares cost
   (`cost_basis <= remaining notional`).

**Edge cases:** partial exit nets proportionally (4 of 10 → 6 remain, next
close books 6-share cost); inclusive earliest-`posted_ts` boundary counts,
one second earlier does not; closes with both/neither side price set do not
net; unmapped tokens do not net (`#793` owns that); refill under the same
pair after full exit books the refill's cost only.

**Out of scope:** `#793` (unmeasurable edge / missing token mapping), `#796`
(market-wide entry freeze), `#797` (pair-cap vs 75% floor), `#798` (ledger
reconciliation report), concurrent-pairs-on-one-token double-credit limit
(stays documented in `_prior_exit_shares`, then retires with the helper).

## Interfaces (locked before logic)

- **New:** `OrderRegistry.get_pair_orders_with_rescue_totals(pair_id) ->
  (fill_rows, rescued_by_token)` where `fill_rows` is exactly what
  `get_pair_orders_with_fill_totals` returns today and `rescued_by_token` maps
  `token_id -> {"shares": float, "cost": float}`. One `_conn()` block, no
  per-order reads. Predicates copied from `rescue_net`, restricted to the
  given `pair_id`; removed cost sums
  `COALESCE(up_cost_removed,0)+COALESCE(dn_cost_removed,0)`.
- **Changed:** `load_pair` return shape UNCHANGED (`heavy/light/legs/naked/fill_cost`);
  only the numbers are netted (`matched = max(0, filled − rescued)`,
  `notional = max(0, filled_notional − rescued_cost)`, `notional = 0` when
  `matched <= SIZE_EPS`, `fill_cost = 0` when the heavy leg is empty). All
  token keys and ordered `orders` lists preserved.
- **Removed call sites:** `_check_positions` stops reading
  `_prior_exit_shares` (prior `0.0`); `exit_single_buy` drops the
  `prior`/`gap` block (`:1142-1147`) and sizes off `_naked_after` on the
  netted `after` view. `_record_exit_close` signature UNCHANGED; it receives
  the netted `after` view and caps `cost_basis`.

## Dependency graph

- T1 (netted inventory view) unblocks T2 (credits are removed against the
  netted view — same change, never separate) and T3 (tests assert netted
  numbers). T1+T2 are one atomic change; T3 proves it.

## Tasks

### T1 — Netted remaining-inventory view [Backend/Logic] · M [x]
- **Target files:** `core_brain/order_registry.py` (new method beside
  `get_pair_orders_with_fill_totals`), `core_brain/single_buy_saver.py`
  (`load_pair`).
- **Build:** add `get_pair_orders_with_rescue_totals`; rewire `load_pair`
  to net per token, rank heavy/light on netted `matched`, compute `naked`
  and `fill_cost` from netted values. Cross-reference comments at both
  netting rules. No netting for unmapped tokens.
- **Depends on:** nothing (risk-first: this is the uncertain core).
- **Verify:** new moe replay test fails before (old pair `naked == 10`,
  second close cost 8.20), passes after.

### T2 — Remove double credit, cap close cost [Backend/Logic] · M [x]
- **Target files:** `core_brain/single_buy_saver.py` only
  (`_check_positions`, `exit_single_buy`, `_record_exit_close`, poll-loop
  review `:2113-2239` + `:2416-2481`).
- **Build:** delete both `_prior_exit_shares` reads in the SAME commit as
  T1; cap `cost_basis = min(size × heavy_avg, netted notional)` and write
  the bounded value to the sold side's removed-cost field; retire
  `_prior_exit_shares` iff grep shows zero callers (move its scoping tests
  to `load_pair`). Poll loop: keep existing skip, use netted legs; change
  settlement-fallback/stranded-exit branches only if they add prior-exit
  credit (expect: none).
- **Depends on:** T1 (same atomic change).
- **Verify:** `test_partially_explained_divergence_still_refuses` still
  refuses (comment now reads "netted 10 vs venue 5"); refill tests keep
  `[10, 10]` sales; one-connection test green; `test_rc_fixes.py` 6.0 green.

### T3 — Focused replay + boundary tests [Debug] · M [x]
- **Target files:** `tests/test_single_buy_saver.py` (moe replay, boundaries),
  `tests/test_refill_after_exit.py` + `tests/test_partial_exit_stays_managed.py`
  (comment/input updates + cost assertions).
- **Build:** moe two-pass replay (d347 fill 10 @ 0.82 → hard-stop sell 10 @
  0.21 ⇒ cost 8.20 / pnl −6.10; 9e5e fill 10 @ 0.15, venue 10, bid 0.129 ⇒
  exactly one 10-share sale, cost 1.50 / proceeds 1.29 / pnl −0.21; parity
  vs `registry_naked_usd` per pair); full-exit zero-legs skip; same-pair
  refill cap (10 @ 0.90 → exit → 10 @ 0.10 → exit books 1.00); ts boundary
  ±1s; both/neither-price exclusion; unmapped-token exclusion; partial-exit
  4-of-10 cost assertion (6 remain, next close 3.60).
- **Depends on:** T1+T2.
- **Verify:** `python -m pytest -q tests/test_single_buy_saver.py
  tests/test_single_leg_lifecycle.py tests/test_refill_after_exit.py
  tests/test_partial_exit_stays_managed.py tests/test_hard_stop_reset.py
  tests/test_order_registry.py tests/test_rc_fixes.py` green; each new test
  fails without the fix (RED checked by stashing T1+T2).

## Checkpoints

- After T1+T2: moe replay passes, focused suites green (one-line report).
- After T3: all focused suites green, ready for Station IV.

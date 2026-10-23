# Plan — #763: pair-scoped close netting

Branch: freebuff/i-pick-issue-763-d85a4853-b8a8-4e1d-863d-f4da0cebbb82 | Issue: #763
Size: Standard (2 modules, one architectural decision, no schema change) | Tasks: 3
Type: [Debug] + [Backend/Logic] — verification: unit/integration runner
Stack: Python, SQLite registry, pytest (`tests/test_single_buy_saver.py`,
`tests/test_order_registry.py` + pinned `test_trader_loop_state.py`,
`test_account_marks.py`, `test_refill_after_exit.py`,
`test_partial_exit_stays_managed.py`)

## Spec (embedded — Small/Standard, no SPEC.md ceremony)

**Problem.** Two condition-level netting loopholes let one close row hide every
later pair's risk on that market: (1) `registry_naked_usd` drops every pair on
a condition with ANY close → re-entered fills count ZERO; (2)
`registry_committed_usd` excludes closed-condition inventory AND resting BUYs →
$0 against the bankroll ceiling; (3) `_prior_exit_shares` sums every
RESCUE_METHODS close on the condition → a prior pair's shares mask a new pair's
venue divergence or net its sale to `route_to_merge`.

**Acceptance.** New pair posted after a close counts fully in naked +
committed; prior pair's rescue shares give ZERO credit to that new pair
(`_check_positions` refuses on a real gap; `exit_single_buy` never nets to
merge); same-pair refill/partial-retry still nets exactly its own booked shares;
all pinned numbers unchanged (or repinned with a comment naming the old bug).

**Out of scope.** Schema change (no `pair_id` on closes); stop/entry/merge
policy; #762's `complete_pair`; #767's query methods; concurrent same-token
pairs (known limit, see task 1).

## CodeRabbit intake note (read once, not twice)

- ADOPTED: time-cutoff eligibility for risk totals; pair-scoped rescue credit;
  gap-cap preservation; per-commit tests; hands-on (non-pytest) verify block.
- REJECTED: nullable `pair_id`/`token_id` columns on `closes` + writer
  stamping. Reason: the issue's Job Guards forbid a `pair_id` join design, a
  migration touches the live-registry code path, and `closes.ts` vs order
  `posted_ts` plus exact booked-share subtraction fully scopes the fix.
- No `[UNVERIFIED]` remainder: ms-vs-s units verified (`now_ms` writes vs
  `time.time()` closes); line ranges spot-checked against the tree.

## Interfaces (locked)

- `_prior_exit_shares(registry, condition_id, token_id, pair_id=None) -> float`
  — new trailing `pair_id`; `None` preserves today's whole-condition sum for
  any legacy caller. Rescue close `c` on the matching side counts iff the pair
  existed when it happened: `c.ts >= min(posted_ts of pair's orders)/1000.0`.
- `_unexplained_divergence` — unchanged.
- `_check_positions(pair, venue_positions, registry)` — passes
  `pair["pair_id"]` through; logic otherwise unchanged.
- `exit_single_buy` netting step (~1092-1112) — same scoped call; order
  (check → cancel → reread → flip → gap-capped net → route → depth → sell)
  unchanged.
- Shared cutoff helper in `order_registry.py`:
  `_close_cutoff_map(registry) -> dict[condition_id, max_ts_seconds]` over ALL
  close methods (any close means the pair's pre-close capital is gone) — used
  by `registry_committed_usd` (Python side) and mirrored as a CTE inside the
  committed inventory SQL.
  `registry_naked_usd`: NO eligibility cutoff (balanced merged pairs already
  read 0 through heavy-minus-light; a cutoff would hide partially-exited
  leftovers). Per-token `matched`/`notional` net of rescue closes with
  `ts >= min(posted_ts)/1000` (`shares` / `up_cost_removed`+`dn_cost_removed`,
  side via `up_price is not null` exactly-one-side, token side from latest
  `quotes` row), clamped ≥ 0, then the existing heavy-minus-light rule.
  `registry_committed_usd`: inventory per `(condition_id, pair_id)` over
  orders with `posted_ts/1000 > cutoff` (cutoff needed: merges leave no SELL
  fill, so inventory cannot self-zero), same rescue netting per group clamped
  ≥ 0; resting BUY counts iff `posted_ts/1000 > cutoff` (or no cutoff), same
  `price × (original − matched)` formula. One SQL statement each; naked does
  one quotes-side lookup in SQL, no per-pair Python loading.

## Dependency graph

T1 (rescue credit) → T3 (committed reuses the scoping rule + existing tests);
T2 (naked) independent of T1. Order risk-first: T1, T2, T3.

## Tasks

### T1 [x] [Debug] Scope `_prior_exit_shares` to pairs alive at close time — S
Files: `core_brain/single_buy_saver.py` (~690-782, ~1092-1112).
Do: add `pair_id` param; `min_posted` from `get_orders_by_pair`; count rescue
closes on the `_token_side` side with `ts >= min_posted/1000`; thread through
`_check_positions` + netting step. Known limit (document in code comment):
two pairs live on one token at once share one venue number — a close between
their opens credits both; refusing-unknown stays safe, note it.
Depends on: —. Verify: new tests below RED before fix, GREEN after.

### T2 [x] [Backend/Logic] `registry_naked_usd`: rescue netting, no cutoff — M
Files: `core_brain/order_registry.py` (~2725-2789).
Do: DROP the `closed_conditions`/`eligible_pairs` exclusion (netting replaces
it: balanced merged pairs read 0 via heavy-minus-light; fully rescued pairs
net to 0). Add `pair_min` CTE + `token_side` (latest quotes side per token) +
`rescue_net` CTEs (RESCUE_METHODS only, exactly-one-side closes, token side
match, `ts >= min_posted/1000`); clamp per-token matched/notional ≥ 0; keep
heavy-light + token-count rules; update docstring (drop "skipped whole").
Depends on: —. Verify: new tests RED→GREEN; `test_account_marks.py` unchanged.

### T3 [x] [Backend/Logic] `registry_committed_usd`: cutoff inventory + resting — M
Files: `core_brain/order_registry.py` (~2792-2835).
Do: reuse `_close_cutoff_map`; inventory per `(condition_id, pair_id)` after
cutoff with rescue netting clamped ≥ 0; resting loop uses cutoff map instead of
`closed_cids`. Update docstring.
Depends on: T1 (scoping rule). Verify: new tests RED→GREEN;
`test_trader_loop_state.py` pins unchanged.
Checkpoint after T1+T2: rescue refusal + naked figure behave; T3 closes the loop.

## Tests (each fails without its fix)

- `tests/test_single_buy_saver.py`: new pair, prior 10-share rescue sale,
  wallet 0 → `PairExitRefused`, no sale, no `route_to_merge`; wallet 5 →
  still refused; same-pair partial (10 fill, 4 sold, wallet 6) → sells 6;
  `exit_single_buy` netting for new pair never routes to merge on a real gap.
- `tests/test_order_registry.py`: untagged close + later imbalanced pair →
  naked counts it; old pair before close stays 0 (both sides of boundary +
  ms/s units pinned); 10-fill/4-rescue → naked = 6 × avg, committed = cost of
  6; full rescue → 0; resting BUY after close counts, before does not.
- Regression pins (no behavior change): `test_refill_after_exit.py`,
  `test_partial_exit_stays_managed.py`, `test_trader_loop_state.py`
  ($10/$6/$4.80), `test_account_marks.py` ($1.86).

## Improvement proposal (adopted — simplification)

One shared `_close_cutoff_map` helper instead of three copies of the
"which conditions have closes" query. Evidence, verbatim: `registry_naked_usd`
builds `WITH closed_conditions AS (SELECT DISTINCT condition_id FROM closes …)`
while `registry_committed_usd` repeats `SELECT DISTINCT condition_id FROM
closes` twice (the `closed_cids` set and the inventory `NOT IN`). Folded into
T2/T3 above.

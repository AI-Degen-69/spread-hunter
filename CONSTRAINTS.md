# CONSTRAINTS.md — Issue #763: pair-scoped close netting

## The three-way risk-figure rule (locks the docstring debate)

`registry_naked_usd` / `registry_committed_usd` docstrings currently call the
condition-wide skip "the safe direction for a risk figure". That framing is
rejected for this issue. The locked rule:

1. **OVER-stating is acceptable.** Counting a leftover or a re-entered pair
   that later proves gone fails safe (caps bite early, exits stay allowed).
2. **UNDER-stating is the bug.** A market that re-qualifies after a merge/exit
   taking new fills that count as ZERO naked/committed exposure is the loss
   this issue fixes. Same for `_prior_exit_shares` letting a prior pair's
   shares explain away a real venue divergence or net `naked` to 0.0.
3. **DOUBLE-counting a merged pair is a new bug.** A close's shares
   (`shares`, `up_cost_removed`, `dn_cost_removed`) may be subtracted at most
   once, against pairs that existed when the close happened. Subtracting one
   close from two live pairs re-books risk the venue no longer carries.

## Hard guards (from the issue + AGENTS.md)

- `data/orders.db` is read-only. All tests use `tmp_path`. Never open,
  rewrite, or delete the production registry.
- `closes` has NO `pair_id` column. No schema change, no `pair_id` join, no
  backfill design. Scope by `closes.ts` (seconds, `time.time()`) against order
  `posted_ts` (milliseconds, `now_ms`), or by subtracting exactly what a close
  booked (`shares`, `up_cost_removed`, `dn_cost_removed`).
- No live venue call: no `--live`, no `order_manager quote|complete`.
- Do not touch stop-loss thresholds, merge execution, `derive_dynamic_caps`
  ratios, or `bot_pos_price_max_pair_cost_usd`.
- Do not edit another branch's code: `single_buy_saver.complete_pair`
  (~1228-1330, #762) and `order_registry` query methods owned by #767 are out
  of scope. Stay in `registry_naked_usd`, `registry_committed_usd`,
  `_prior_exit_shares`, `_check_positions`, and the `exit_single_buy` netting
  step (~1092-1112).
- Time-unit discipline: `posted_ts` is ms, `closes.ts` is s. Every comparison
  converts explicitly (`posted_ts / 1000.0` or `cutoff * 1000`). Boundary
  direction is pinned by tests on BOTH sides: posted at or before the cutoff
  stays hidden (keeps legacy fixtures green), posted after counts.
- Pinned numbers that must not move without a comment naming the old value as
  the bug: `tests/test_trader_loop_state.py` (`_fleet_state`,
  `registry_committed_usd` $10 / $6 / $4.80), `tests/test_order_registry.py`
  close tests, `tests/test_account_marks.py` ($1.86, unfilled, malformed).
- Existing refusals stay: gap-capped netting only ever shrinks a sale toward
  the venue view, never grows one; every fail-closed `PairExitRefused` path is
  preserved. The stray-guard `exit_single_buy` path (no `reason=`) changes live
  exits, not just a report — treat it as live code.
- Out-of-scope findings go to `docs/issues/763-noticed-but-not-touching.md`
  with status `open`, an owner, and a when. Never expand scope in the fix.

## Verification contract

- Agent runs focused tests silently (RED/GREEN loop per task). Never ask the
  operator to run `pytest`; never present counts as proof.
- The closing report's How-to-verify block is hands-on only: shadow rehearsal
  on a fresh store + dashboard risk panel (fleet naked USD non-zero while a
  re-entered market is open). Five steps or fewer, expected vs failed value per
  step, per `docs/agents/verifying.md`.

# Plan — #628 Atomic stop-loss exit

Issue: #628 · Branch: `i628/atomic-stop-loss-exit` · Base: `origin/main` @ `4dbf1b1` (0 behind, 2026-10-16)
Size: Small (3 tasks) · Baseline: 291 passed (lifecycle + saver + trader_loop)

## 1. Problem statement

When a stop-loss fires on a filled leg, the engine must leave zero orphaned
state: market-sell the filled leg AND cancel the opposite resting maker quote,
then reset the pair so the market quotes freshly.

Already holds (verified verbatim):

- `single_buy_saver.py:866-899` (`exit_single_buy`): cancels BOTH legs first,
  re-reads the venue (`_venue_matched`), sells only if still one-sided.
- `single_buy_saver.py:1793-1805`: poll STOP block couples
  `action == "hard_stop"` to `exit_single_buy(reason="lifecycle_hard_stop",
  force=True)` and tags `route="hard_stop"`.
- `trader_loop.py` visit path cancels both pair orders on HARD_STOP and never
  submits a buy (`test_hard_stop_is_terminal_and_never_submits_a_buy`).

Two gaps this plan closes:

1. No test pins cancel-before-sell ordering or the cancel-fail / opposite-fill
   refusal on the `route="hard_stop"` path.
2. The HEDGE SHIELD (`trader_loop.py:980`: `{"ESCALATED_HEDGE", "HARD_STOP"}`)
   keeps HARD_STOP pairs protected after a successful exit, so a dropped market
   retains resting orders instead of cleaning up. Shield narrows to
   `ESCALATED_HEDGE`; the exit leaves a terminal row the shield no longer matches.

## 2. Grounded seams (verified verbatim)

| Seam | Location | Contract |
| --- | --- | --- |
| `transition(...)` → `action="hard_stop"` | `single_leg_lifecycle.py:350-430` | Pure policy; WAIT/HEDGE return wait/hedge (never cancel) |
| `evaluate(...)` persists on change | `single_leg_lifecycle.py:573-611` | Skips write when refused or unchanged |
| `persist_decision(...)` | `single_leg_lifecycle.py:523-570` | Writes LifecycleStateRecord |
| `exit_single_buy(...)` | `single_buy_saver.py:784+` | Cancel both → `_venue_matched` → sell-if-one-sided; else `PairExitRefused` |
| `_venue_matched(client, orders)` | `single_buy_saver.py:523-625` | Post-cancel venue truth |
| `_completion_target(client, pair_id)` | `single_buy_saver.py:640-660` | Merge-sweep race guard |
| Poll STOP block | `single_buy_saver.py:1768-1805` | get state → transition → persist HARD_STOP → exit(force) → route=hard_stop |
| Dropped-market shield | `trader_loop.py:836-896` + `980` | ESCALATED_HEDGE/HARD_STOP rows keep resting orders; resolved exits first |
| `plan_orders(...)` | `trader_loop.py:210+` | Visit cancel routing; HARD_STOP visit cancels both legs |
| `get/save_lifecycle_state` | `order_registry.py:1121+` | Shield + persistence store |

Must NOT touch: `risk.py`, `order_registry.py`, `order_manager.py`, `config.py`,
`quotes.py`, `shadow_run.py`, `unhedged_stop_loss.py`, `data/*.db`. No threshold /
tick / floor / `max_pair_cost` change. No new LegState. No taker-ask re-gating.

## 3. Assumptions & risks

- Builder reuses `exit_single_buy` for hard-stop (already wired); no new exit fn.
- HARD_STOP persisted before execution stays (restart-observability).
- PATIENT_WAIT / ESCALATED_HEDGE never cancel (resting maker is protection).
- Risk: shield narrowing changes dropped-market cleanup for stopped pairs —
  intended; T1 regression test covers it.
- Resolved contradiction: brief names `stop_memory`, `market_sell`,
  `execute_exit` — none exist in tree (searched 2026-10-16). Real seams are the
  loop-local `stop_state` dict and `exit_single_buy`. Plan uses real names.

## 4. Improvement proposal (adopted — simplification)

> Evidence (issue #628 body): "when a filled position hits its stop-loss
> trigger, the filled leg is dumped via market sell AND the opposite resting
> order is immediately cancelled, leaving zero orphaned orders or hanging
> state."

After a FULLY sold hard-stop exit, reset the pair lifecycle row to
`DUAL_RESTING` (existing "no exposure" state) instead of leaving `HARD_STOP`.
`HARD_STOP` means "exit this now" — once nothing is held it is a stale
imperative that keeps matching the shield and blocks fresh quoting.
`DUAL_RESTING` needs no new enum. Partial exits keep `HARD_STOP` so the
remainder stays managed. Folded into T3.

## 5. Tasks (risk-first, dependency order)

### T1 — Narrow HEDGE SHIELD to ESCALATED_HEDGE [S, policy]

- Depends on: nothing (redefines "protected" for T2/T3).
- `trader_loop.py:980`: `_HEDGE_PROTECTED_STATES = frozenset({"ESCALATED_HEDGE"})`.
  Update comments at 976-979 and 836-844: HARD_STOP pairs clean up (exit already
  cancelled both legs; nothing to protect).
- `tests/test_trader_loop.py`: dropped-market test — HARD_STOP row pair gets
  orders cancelled (no `dropped_market_hedge_protected` row), ESCALATED_HEDGE
  pair on same sweep stays protected.
- Verify: `python -m pytest -q tests/test_trader_loop.py -k "hedge or dropped or shield"`.

### T2 — Pin atomic cancel-then-sell on hard-stop exit [M, executor]

- Depends on: T1.
- `single_buy_saver.py`: no production change expected (guard at 866-899
  already cancels both → re-reads → sells-if-one-sided). Fix only if a test
  below exposes a hole; otherwise tests-only.
- `tests/test_single_buy_saver.py` (FakeClient + `_one_sided_pair`, no network):
  1. `test_hard_stop_exit_cancels_opposite_before_selling` — heavy UP filled 10
     @ 0.60, light DOWN open; `cancelled` holds both venue ids; `cancel:*`
     precedes `sell:*`; SOLD close recorded.
  2. `test_hard_stop_exit_refuses_when_opposite_fills_in_window` — OvertakingClient
     fills light leg pre-`_venue_matched` (mirror lines 914-936); expect
     `PairExitRefused`, zero `sell:` (completed pair → merge, never sold).
  3. `test_hard_stop_exit_refuses_when_cancel_fails` — `cancel_ok=False`; expect
     `PairExitRefused`, zero `sell:`.
- Verify: `python -m pytest -q tests/test_single_buy_saver.py`.

### T3 — Terminal reset after full exit + fresh-quote regression [M, lifecycle]

- Depends on: T2.
- `single_buy_saver.py` STOP block (~1793-1805): after `exit_single_buy` returns
  `action == "exited"`, re-read venue exposure; if zero held on both tokens,
  overwrite row via `save_lifecycle_state(... state="DUAL_RESTING",
  reason="hard_stop_exited" ...)`. Partial exits keep `HARD_STOP`.
- `tests/test_single_buy_saver.py`: `test_hard_stop_full_exit_resets_lifecycle`
  (all 10 sold → `DUAL_RESTING`); `test_hard_stop_partial_exit_keeps_hard_stop`
  (depth sells 4/10 → still `HARD_STOP`).
- `tests/test_single_leg_lifecycle.py`: `test_patient_wait_never_cancels`,
  `test_escalated_hedge_never_cancels` (pure `transition` returns wait/hedge).
- `tests/test_trader_loop.py`:
  `test_stopped_out_pair_does_not_block_fresh_quote` — next visit same market,
  normal spread → ordinary intents, new pair id.
- Verify: `python -m pytest -q tests/test_single_leg_lifecycle.py tests/test_single_buy_saver.py tests/test_trader_loop.py`.

Checkpoints: after T1 (shield narrowed, green); after T2 (atomicity pinned);
after T3 (full plan green → handoff to iii-build-plan).

## 6. Verification & TDD

- RED first per task; T2 may stay green (guard predates plan — record it).
- Regression (agent-run, never operator):
  `python -m pytest -q tests/test_single_leg_lifecycle.py tests/test_single_buy_saver.py tests/test_trader_loop.py`
  (baseline 2026-10-16: 291 passed).
- Operator How-to-verify (build time, hands-on, ≤5 steps): shadow rehearsal on a
  fresh shadow DB — one hard-stop close, no resting orders on old pair, later new
  pair same market. Never pytest/gh/git/CI. Never `data/orders.db`. Never live
  quote/complete/loop/START.

## 7. Out-of-scope

- Threshold tuning, taker-ask re-gating (#626), merge routing, renames of
  nonexistent `stop_memory`/`market_sell`/`execute_exit` — documented, untouched.

## 8. Interfaces locked

- `transition` / `evaluate` / `persist_decision` / `exit_single_buy` unchanged.
- Sole constant change: `_HEDGE_PROTECTED_STATES` → `{"ESCALATED_HEDGE"}`.
- Rows: `HARD_STOP` (pending/partial) → `DUAL_RESTING` (fully exited).



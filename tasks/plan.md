# Plan — #525 · [FIX] Stop-loss exits do not record the trigger bid

`Branch: freebuff/ii-plan-issue-525-2433c4be-7d14-441b-999b-3cf6ef082ed3 | Issue: #525`

## 0. Classification

- **Size tier: Small** — one function (`_route_pair`), two keyword arguments in one file, plus
  regressions in one test file. Straightforward once the code is read; no new dependency, API, or
  schema change.
- **Task type: Code, Debug** — a bug fix to close-recording on two existing exit routes.
- **Quick-fix lane:** not offered — the issue carries no `quick-fix` label (labels: `bug`,
  `ready-for-agent`), so no gate runs per Step 0C.

## 1. Spec (embedded — Small tier, no SPEC.md ceremony)

**Goal:** the adverse-drift stop loss and the grace-expiry/ladder timer in `_route_pair` record
the bid that tripped the check, so latency drag on those exits is measured, not assumed.

**Acceptance criteria (from the issue, verbatim intent):**
1. A forced adverse-drift exit in a shadow rehearsal records a non-NULL `trigger_bid` equal to
   the bid that tripped the check.
2. For a store whose only rescue closes came from the adverse-drift route, the `assumed_offset`
   count is 0.
3. `.venv/Scripts/python.exe -m pytest -q tests/test_single_buy_saver.py
   tests/test_rescue_friction_analytics.py` passes (venv-activated equivalent).

**Edge cases:**
- Timer fires with no decision bid (`heavy_bid is None`, empty book): close keeps
  `trigger_bid=NULL` / `assumed_offset`. Never substitute the later execution bid.
- Adverse-drift check requires `heavy_bid is not None` already (`:2041`); no new None-path there.
- Only ladder-stamped pairs (`ladder-…` + `ladder_mode`) reach these exits; ordinary pairs keep
  lifecycle behaviour.

**Explicit out-of-scope (issue Scope):** stranded-unhedged exit (`:1899`) and stray-guard path
untouched — "the stop loss should catch those conditions earlier, so those routes are not the
target of this fix." No threshold, trigger-condition, or drag-formula change — "purely records a
price that is already computed."

## 2. CodeRabbit plan intake (read once; echo ignored)

Adopted: two-kwarg fix shape; all regressions in `tests/test_single_buy_saver.py` so the ticket
gate covers them; ladder-pair setup + `_cfg()` pattern copied from `tests/test_ladder_exit.py`
(UP 10 @ 0.60, DOWN working @ 0.38; thresholds 0.10 / 0.045, `ladder_exit_sec=60.0`); DOWN ask
0.40 stub to hold completion above the 0.995 cap; cancel-gated book stub proving two separate
reads; timer-None guard; hermetic shadow case via `ShadowExecutionClient` + `ensure_shadow_tables`
per `tests/test_hard_stop_fresh_exit.py:16-33`; hands-on shadow-only operator check.
Rejected: over-split phasing — merged into 3 atomic tasks below; any new operator tooling or
threshold/config change to force a fill (out of scope, forbidden by CONSTRAINTS.md).
Still `[UNVERIFIED]` (resolve from code in Station III, test-setup-only fixes): the exact helper
that builds the `pair` dict argument for `_route_pair`; shadow-client read/cancel order for the
bid switch; the full `manage_single_leg_positions` config contract beyond `_cfg()`; whether a
shadow adverse-drift sell fills without a seeded queue snapshot (fallback: seed per
`test_hard_stop_fresh_exit.py:36-45`, or drop the shadow test and note the gap in the PR).

## 3. Open questions

None. The issue answers what to change and what to leave; the two `[UNVERIFIED]` seams above are
test-setup details Station III resolves from the code, not operator questions.

## 4. Frozen interface contract

- `exit_single_buy(client, registry, pair_id, max_pair_cost, live, venue_positions, reason,
  method, *, force=False, trigger_bid=None)` — signature frozen; the fix passes the existing
  `trigger_bid` kwarg at two call sites. Verified at `single_buy_saver.py:777-789`.
- Close-row keys `trigger_bid` / `fresh_bid` / `reason` / `method` (`:991-994`, `:755-756`,
  `:1007`); `None` persists as SQL NULL. Frozen.
- `rescue_friction_summary(closes)` keys `recorded_reference_count` /
  `assumed_reference_count`; counted methods `single_buy_exit`, `naked_exit`, `ladder_exit`
  (`statistical_analytics.py:586`). Frozen.
- `_stop_reference`: finite `trigger_bid` → `("recorded")`, else `entry - 0.04` →
  `("assumed_offset")` (`:396-413`, `STOP_OFFSET_FALLBACK = 0.04` at `:130`). Logic frozen;
  docstring only (see §5).

## 5. One improvement proposal (adopted — simplification/docs hardening)

**Evidence (verbatim, `statistical_analytics.py:522-524`):**
> "`trigger_bid` is the recorded bid that fired the stop. Only the lifecycle hard stop
> writes it, so the other rescue routes fall back to `entry - STOP_OFFSET_FALLBACK`,"

**Proposal:** after the fix that sentence is stale — three routes write it. Update the docstring
to name the writers (lifecycle hard stop + both `_route_pair` exits) and keep the fallback
description for the remaining routes. Adopted by default (no behaviour change, prevents the next
reader from mistrusting the recorded references). Recorded here so it does not resurface.
No scope expansion proposed; stranded/stray routes stay untouched per the issue.

## 6. Task decomposition (dependency graph first, risk-first order)

- T1 (adverse-drift record + assumed-zero) unblocks nothing technically but de-risks the primary
  route first; T2 (timer + guard) is independent of T1's fix but follows it so the shared stub
  lands once; T3 (shadow + gate) depends on T1+T2 green.

### T1 — Adverse-drift exit records the trigger bid [Code/Debug] · size S
- **Depends on:** none. **Touches:** `tests/test_single_buy_saver.py` (stub + 2 tests),
  `core_brain/single_buy_saver.py` (`_route_pair` adverse-drift call ~:2048-2050).
- **Build:** copy `_ladder_pair` + `_cfg()` pattern from `tests/test_ladder_exit.py`; book stub
  (DOWN ask 0.40; UP decision bid until first cancel, execution bid after; empty bid list = no
  bid); `test_ladder_adverse_drift_exit_records_trigger_bid` (pair 10s old, bids 0.50→0.49;
  assert `action == "exited"`, reason `adverse_drift`, one close method `ladder_exit`,
  `trigger_bid == 0.50`, `fresh_bid == 0.49`, bids unequal; RED = `trigger_bid is None`);
  `test_ladder_adverse_drift_only_store_has_zero_assumed_references` (same setup through
  `rescue_friction_summary`; assert assumed 0, recorded 1; RED = assumed 1). Then add
  `trigger_bid=heavy_bid` to the adverse-drift call; keep reason/method/branch order.
- **Verify:** the two new tests flip RED→GREEN; `tests/test_rescue_friction_analytics.py`
  unchanged-green.
- **Checkpoint:** primary stop-loss route measured; assumed count already 0.

### T2 — Timer exit records the trigger bid, None stays NULL [Code/Debug] · size S
- **Depends on:** T1 (reuses its stub). **Touches:** same two files (timer call ~:2065-2067).
- **Build:** `test_ladder_timer_exit_records_trigger_bid` (pair 200s old, `ladder_mode=True`,
  bids 0.57→0.56, 0.03 loss below both thresholds; assert reason + method `ladder_exit`,
  `trigger_bid == 0.57`, `fresh_bid == 0.56`; RED before timer fix);
  `test_ladder_timer_exit_without_decision_bid_keeps_trigger_null` (empty decision bids,
  exec 0.56; assert reason `ladder_exit`, `trigger_bid is None`, `fresh_bid == 0.56` — guard,
  green before and after, forbids substituting `fresh_bid`). Then add `trigger_bid=heavy_bid`
  to the timer call, passed through including `None`.
- **Verify:** timer-record test RED→GREEN; guard green throughout.
- **Checkpoint:** both `_route_pair` exits covered; production diff is exactly two kwargs.

### T3 — Forced shadow case + gate + operator check [Code/Debug] · size M
- **Depends on:** T1, T2. **Touches:** `tests/test_single_buy_saver.py` (shadow test),
  `core_brain/statistical_analytics.py` docstring line (§5).
- **Build:** `test_shadow_ladder_adverse_drift_records_trigger_bid` (temp registry,
  `ensure_shadow_tables`, `ShadowExecutionClient(book_fn)` per `test_hard_stop_fresh_exit.py`;
  T1 values 0.40/0.50→0.49; seed one ladder-stamped pair; drive
  `manage_single_leg_positions(...)`; assert one close reason `adverse_drift`,
  `trigger_bid == 0.50` non-NULL; no exit-price/drag assert — snapshot behaviour may differ
  from `fresh_bid`). Fill trouble → seed queue snapshot per `test_hard_stop_fresh_exit.py:36-45`;
  still unreliable → drop the test, keep T1 evidence, note the gap in the PR. Never touch
  `shadow_exec.py`/config to force a fill.
- **Verify (agent-run):** ticket gate `tests/test_single_buy_saver.py` +
  `tests/test_rescue_friction_analytics.py`; oracle runs `tests/test_ladder_exit.py`,
  `tests/test_dual_stop_loss.py`, `tests/test_hard_stop_fresh_exit.py`. Full suite stays with CI.
- **Operator "How to verify" (for the PR, hands-on only):** fixture rehearsal to a new shadow
  store → read-only dashboard → `settlements[]` bids → friction counts → optional report script
  (exact commands written in Station IV; at most five steps, no pytest, new `NN_` store, never
  `data/orders.db`, no START/live commands). Fixture may yield no adverse-drift close — then the
  T3 test is the forced-case evidence; green latency badge alone is not evidence.

## 7. Handoff

Next station: `iii-build-plan auto` on this branch. Build order T1 → T2 → T3 with RED→GREEN per
task. Do not invoke `@coderabbitai autofix`; no second review loop in this pipeline.

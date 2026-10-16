# Plan — #603 [REFACTOR] Delete the _route_pair exit helper that no live code calls

Branch: i603/refactor-delete-the-route-pair-exit-helper-that | Issue: #603
Size: Standard (5 files, internal test migration + 1 deletion; no behavior change) | Type: Code
Labels: ready-for-agent (no `quick-fix` label → Step 0C gate skipped, no lane offered)
No `needs-answers` label and no Open-questions section → no operator questions.

## Spec (embedded; issue body is the authority)

`_route_pair` lost its last production caller when the short-window ladder path
was removed (#589). Only tests call it. Delete the helper and move its coverage
(completion, adverse-drift exit, grace-hold, trigger-bid recording from #525)
onto the unified lifecycle (`manage_single_leg_positions` + generic single-buy exit).
Out of scope: any lifecycle/exit-semantics change, the `ladder_exit` taxonomy
string (#558–560 chain), any other dead-code removal.

Acceptance:
- `grep -rn "_route_pair" core_brain --include="*.py"` returns nothing.
- `py -3.12 -m pytest -q tests/test_single_buy_saver.py tests/test_refill_after_exit.py tests/test_exit_price_realism.py tests/test_grace_sweep_report.py` passes.

## CodeRabbit plan intake (non-binding scaffolding; read once, verified against live code)

Adopted: 3-phase shape (move coverage in both test files, then delete);
hard-stop vs settlement-fallback mapping for the legacy exits; settlement exits
keep `trigger_bid` null (no exit-semantics change); monkeypatched test clock for
the refill two-pass test; one new live settlement-completion test inside the
acceptance files; docstring-only cleanup in `core_brain`.
Rejected: the 4+-task split was merged into 3 atomic tasks; nothing else invented.
Resolved `[UNVERIFIED]` items:
- "Hard-stop test in tests/test_auto_pairs.py" — NO SUCH TEST (zero `hard_stop`
  hits in that file). The `lifecycle_hard_stop` reason string is pinned at
  executor level in tests/test_hard_stop_fresh_exit.py. Build derives the
  manager-level assertion shape from live `manage_single_leg_positions` +
  `exit_single_buy` close shape instead.
- Shadow manager call shape cited as shadow_run.py:1229–1240 — actual call at
  ~1245 (`manage_single_leg_positions(exec_client, seam.registry, cfg,
  venue_positions=..., market_state_fn=..., state_cache=..., resolved_cids=...)`).
  Re-derive line numbers at build; paths are the stable contract.
- Verified present: `_route_pair` def at single_buy_saver.py:1987; #525 section
  at test_single_buy_saver.py:1634–1860 with `_trig525_*` helpers +
  `DecisionThenExecutionBook`; refill tests at test_refill_after_exit.py:190–230;
  docstrings naming `_route_pair` at shadow_run.py:364,
  statistical_analytics.py:526; stranded tests + `FakeClient`/`_one_sided_pair`
  in test_single_buy_saver.py (~1470); `ladder_exit` accounting test (~1547);
  patient-wait test at test_auto_pairs.py:294–307.
- test_grace_sweep_report.py and test_exit_price_realism.py contain NO
  `_route_pair` callers (comment-only refs at :179 and :8) — they are
  acceptance-suite green-checks, not migration work.
- Manager cfg names verified in tests/test_aged_out_rescue.py:
  `enable_aged_out_rescue`, `pairs_exit_window_sec=900`,
  `aged_out_rescue_lead_sec=900`.

## Interface contract

No interface change: `manage_single_leg_positions` signature unchanged, no new
public API, no config/schema change. Only a private helper is deleted and tests
are re-homed. (Step 4 detail skipped — deletion, not a contract.)

## Improvement proposal (adopt-by-default, rejectable in one line)

Evidence (verbatim, tests/test_grace_sweep_report.py:179): ``# `_route_pair`
exits on the first poll AFTER grace expires, so a grace`` — this comment will
describe a deleted helper after Task 3, so reword that one comment to name the
unified lifecycle instead of leaving a stale pointer.

## Dependency graph

- T1 (move #525 coverage + add completion test) and T2 (move refill/wait tests)
  are independent of each other; T3 (delete + docstrings + checks) depends on
  both — coverage must never drop.

## Tasks

### T1 [M, Test migration] Move #525 trigger-bid coverage onto the manager — tests/test_single_buy_saver.py
Depends on: none. Skill: test-driven-development.
Status: [x] done — coverage drives `manage_single_leg_positions`; helper still present (T3 deletes it).
- Rework `_trig525_cfg`/`_trig525_route` in place (no new helper): cfg gets
  pairs rule on, aged-out rescue on, `max_pair_cost=0.995`, 900s window, 900s
  settlement lead; route calls `manage_single_leg_positions(live=True, now=...,
  venue_positions={UP:10, DOWN:0}, market_state_fn=...)`.
- Adverse-drift tests → hard-stop exits (decision bid 0.15 edge, execution
  0.14, DOWN ask 0.40, no market_state_fn): one close, reason
  `lifecycle_hard_stop`, `trigger_bid==0.15`, `fresh_bid==0.14`, one 10-UP SELL,
  no BUY; keep the 1-recorded/0-assumed friction assertion.
- Timer tests → settlement-fallback exits (fill venue_ts=1_000_000, now=3900,
  open accepting market ending now+600, DOWN ask 0.40 refuses completion):
  `route=="exited"`, `action=="aged_out_rescue"`,
  `lifecycle_stage=="settlement_fallback"`, `fresh_bid==0.56`,
  `trigger_bid is None`. Never expect `reason="grace_expired"`.
- Shadow test → hard-stop through the manager (real ShadowExecutionClient, temp
  shadow DB, MakerConfig; bids switch after cancellation).
- New: one live settlement-completion test next to the stranded tests
  (`route=="completed"`, one DOWN BUY at 0.30 for 3.0 USDC, no SELL, no close).
- Verify: `py -3.12 -m pytest -q tests/test_single_buy_saver.py` (agent-run).

### T2 [M, Test migration] Move refill/wait coverage onto manager passes — tests/test_refill_after_exit.py
Depends on: none. Skill: test-driven-development.
Status: [x] done — both cases drive `manage_single_leg_positions` with the manager's clock.
- Extend `_cfg` with the same manager settings; drop the grace arg where dead.
- Two refill cases → one `test_manager_exits_the_refill_on_second_pass` with a
  monkeypatched mutable clock on the `time.time` used by
  `core_brain.single_buy_saver` (fill@1_000_000ms → pass1@3900s/end 4500s →
  refill@3_901_000ms → pass2@5000s/end 5600s; FakeVenue bid 0.57/ask 0.40):
  both passes exit, second SELL exactly 10, total 20, exactly two closes.
- Fresh-fill test → `test_fresh_fill_waits_patiently` (1s-old fill, no
  market_state_fn, `live=True`, held bid from test_auto_pairs.py:294–307):
  reports `patient_wait`, no orders/cancels.
- Leave direct executor tests (:122–186) untouched.
- Verify: `py -3.12 -m pytest -q tests/test_refill_after_exit.py` (agent-run).

### T3 [S, Deletion] Delete `_route_pair`, reword 2 docstrings, run acceptance checks
Depends on: T1, T2. Skill: incremental-implementation.
- Delete only `_route_pair` (single_buy_saver.py:1987–2064); keep `should_exit`,
  `best_ask/bid`, `complete_pair`, `PairCompletionRefused`, `exit_single_buy`,
  `exit_naked_leg` alias, and all config fields. Remove leftover imports in the
  two test files.
- Reword text only: shadow_run.py:364 (explicit-settings point, no
  `_route_pair` name); statistical_analytics.py:526 `_stop_reference` (hard
  stop writes `trigger_bid`, settlement fallback leaves it null).
- Checks (agent-run, report only what ran):
  `grep -rn "_route_pair" core_brain --include="*.py"` → empty; the 4-file
  acceptance suite passes; focused regression on tests/test_auto_pairs.py,
  tests/test_aged_out_rescue.py, tests/test_single_leg_lifecycle.py.
- If a migrated test fails, fix test setup first (timestamps, venue holdings,
  book switching) — never change lifecycle code to make a test pass.

Checkpoints: after T1+T2 (coverage moved, helper still present); after T3 (done).

## How to verify (hands-on, no pytest)

- Run `grep -rn "_route_pair" core_brain --include="*.py"` yourself — empty
  output is the deletion proof.
- Open tests/test_single_buy_saver.py and find
  `test_hard_stop_exit_records_trigger_bid` driving
  `manage_single_leg_positions` instead of `lp._route_pair`.
- Behavior is unchanged by design (tests only + one dead-helper deletion), so
  no dashboard/shadow change is observable; no shadow rehearsal required.

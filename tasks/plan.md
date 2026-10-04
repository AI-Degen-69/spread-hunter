Branch: i361/dead-band-rehearsal | Issue: #361

# Implementation Plan — Issue #361: Dead-Band Rehearsal Evaluation

## Intake & Context Analysis
- **CodeRabbit Plan Intake**:
  - Adopted:
    - Regression test in `tests/test_trader_loop.py` asserting pair-cost re-gate behavior across narrow (`0.03`) and wide (`0.08`) dead bands, checking cancel reason `price_moved` vs `regate_pair_cost`.
    - Signer-free concurrent shadow rehearsal across control (`0.03`) and treatment (`0.08`) using frozen universe snapshot and isolated scratch DBs.
    - Trial memo structure in `docs/runs/` comparing order lifetimes, cancel reasons, queue multiples, open order counts, and explicit verdict relative to #360.
  - Rejected:
    - CodeRabbit Task 1.2 proposed extending `statistics_report.py` / `cancel_report.py` if #359 was missing. #359 is already merged and landed (`bc36b64`), so report CLI / model re-engineering is unnecessary.
    - Over-engineered book tape recording pipeline that touches untested file paths.
  - [UNVERIFIED]:
    - Live market volatility during the trial window: whether sufficient >3c price movements occur to generate non-zero cancels (to be resolved empirically during execution).

- **Open Questions Resolution (from codebase & history)**:
  1. *How wide should the band be?* Codebase default is `0.03` (`core_brain/config.py:846`). As analyzed in CodeRabbit and issue history, `0.08` provides a meaningful delta above the `0.05` `hold_below_target` while remaining below the 36c p90 "market leaving" threshold.
  2. *Is a dead-band change safe given the pair-cost re-gate?* Yes, verified from `core_brain/trader_loop.py:233-261`: `regate_blocks` cancels the order regardless of whether the price move was within or outside the dead-band tolerance. A regression test will permanently lock this behavior.
  3. *Does widening the band simply convert cancels into stale open orders?* Evaluated by tracking orders remaining in `status='open'` (censored age) alongside terminal cancellations in the report.
  4. *What was the result of #360?* #360 concluded as **INCONCLUSIVE (Underpowered)** because in a 4.1-minute window, market prices fluctuated by < 3c resulting in 0 cancels and 0 fills in both arms. The default `requote_hold_queue_shares = 0.0` was kept unchanged.

## Improvement Proposal
- **Proposal (Simplification / Edge-Case Hardening — Adopted)**:
  Evidence: `core_brain/trader_loop.py:231-262` and `docs/runs/2026-10-04-queue-hold-200-rehearsal.md:61-68`.
  In calm markets, price moves rarely exceed 3c over a short duration, leading to 0 cancels and inconclusive results. The plan establishes strict pre-registered decision criteria: if the rehearsal encounters calm market conditions resulting in <1,600 quotes and 0 cancels, the trial must be declared **INCONCLUSIVE** and the default `requote_dead_band = 0.03` must remain strictly unchanged. Additionally, the regression test explicitly asserts that even if `hold_below_target` and `hold_queue_shares` are active, a pair exceeding `max_completable_pair_cost` is immediately rejected.

## Dependency Graph
- Task 1 (Pair-Cost Re-Gate Regression Tests)
  └──> Task 2 (Execute Paired Shadow Rehearsal Arms)
        └──> Task 3 (Author Trial Memorandum & Final Verification)

---

## Tasks

### Task 1: [Backend/Logic] Add Pair-Cost Re-Gate Dead-Band Invariance Tests
- **Size**: S
- **Domain Tag**: `[Backend/Logic]`
- **Helper Skill**: `test-driven-development`
- **Target Files**: `tests/test_trader_loop.py`
- **Depends on**: None
- **Description**: Add unit tests in `TestPlanOrders` verifying that:
  1. When resting price + hedge ask fails `max_completable_pair_cost`, the order is cancelled under both `dead_band=0.03` and `dead_band=0.08`.
  2. Under `dead_band=0.03`, an out-of-band price move assigns `cancel_reason = "price_moved"`.
  3. Under `dead_band=0.08`, an in-band price move assigns `cancel_reason = "regate_pair_cost"`.
  4. Cost failure prevents holding even when `hold_below_target` and `hold_queue_shares` are active.
- **Verification**: `python -m pytest -q tests/test_trader_loop.py -k test_dead_band`

### Task 2: [Execution/Rehearsal] Execute Paired Shadow Rehearsal (0.03 Control vs 0.08 Treatment)
- **Size**: M
- **Domain Tag**: `[Execution/Rehearsal]`
- **Helper Skill**: `incremental-implementation`
- **Target Files**: `data/361_shadow_control.db`, `data/361_shadow_wide08.db`, `data/scratch_markets_frozen_361.json`
- **Depends on**: Task 1
- **Description**:
  1. Capture a frozen candidate market universe snapshot `data/scratch_markets_frozen_361.json`.
  2. Execute paired concurrent shadow runs using `core_brain.shadow_run` without signers:
     - Control: `HUNTER_REQUOTE_DEAD_BAND=0.03` with DB `data/361_shadow_control.db` and run ID `shadow-control-361`.
     - Treatment: `HUNTER_REQUOTE_DEAD_BAND=0.08` with DB `data/361_shadow_wide08.db` and run ID `shadow-wide08-361`.
  3. Ensure `data/orders.db` is strictly untouched.
  4. Generate statistics reports via `core_brain.statistics_report::write_statistics_report`.
- **Verification**: Verify both scratch databases and generated markdown reports in `reports/`.

### Task 3: [Research/Docs] Author Trial Memorandum in `docs/runs/`
- **Size**: M
- **Domain Tag**: `[Research/Docs]`
- **Helper Skill**: `documentation-and-adrs`
- **Target Files**: `docs/runs/2026-10-04-shadow-dead-band-trial.md`
- **Depends on**: Task 2
- **Description**:
  1. Record run parameters, execution windows, and side-by-side metrics: quote counts, distinct orders, cancellations, cancel-reason mix, order lifetime (terminal vs open), queue multiples, and fill rates.
  2. Evaluate whether price-moved cancels killed reachable orders or were market-leaving, and whether a single scalar dead band can capture this distinction.
  3. Document the pair-cost re-gate safety check citing Task 1's regression test.
  4. Provide an explicit verdict (Adopt, Reject, or Inconclusive) and state the relationship to #360's inconclusive queue-hold findings.
  5. State clearly that shipped defaults in `core_brain/config.py` remain unchanged.
- **Verification**: Verify markdown structure and run targeted test suite:
  `python -m pytest -q tests/test_trader_loop.py tests/test_statistics_report.py`

---

## Checkpoints
- Checkpoint 1 (after Task 1): Regression tests pass, proving cost-gate invariance across dead-band widths.
- Checkpoint 2 (after Task 2): Both shadow arms complete cleanly with isolated telemetry reports.
- Checkpoint 3 (after Task 3): Full trial memo authored with evidence-backed verdict; all targeted suites green.

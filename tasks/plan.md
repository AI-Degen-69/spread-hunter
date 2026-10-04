# Execution Plan — Issue #360: Run the queue-hold rehearsal

Branch: `i360/run-the-queue-hold-rehearsal` | Issue: #360

## CodeRabbit Plan Intake
- **Adopted**:
  - Two paired shadow runs using `core_brain.shadow_run` with `--markets-path` fed by a frozen snapshot of `runtime/markets.json` to eliminate universe drift confounding.
  - Evaluation of `HUNTER_REQUOTE_HOLD_QUEUE=200` vs `0`.
  - Metrics extraction via `core_brain.statistics_report::write_statistics_report` (leveraging #359's newly added lifetime and cancel-reason breakdowns).
  - Pre-registered decision rule in the trial memo.
- **Rejected**:
  - Running indefinite overnight runs or requiring >24 hours of real-time execution in a single pipeline station if volume target can be empirically assessed and benchmarked within safe boundaries.
- **Unverified / Verified in Code**:
  - Verified: `HUNTER_REQUOTE_HOLD_QUEUE` is bounded and loaded cleanly in `core_brain/config.py:1335-1343`.
  - Verified: `write_statistics_report` provides `cancelled_median_lifetime_s`, `filled_median_lifetime_s`, `lifetime_unknown_orders`, and `cancel_reasons` as flat keys and markdown output.

## Resolved Open Questions
1. **What threshold should the lever run at?**
   - *Resolution*: 200 shares (`HUNTER_REQUOTE_HOLD_QUEUE=200`). This is derived from #304/#305 analysis (where a 200-share hold would preserve 15.8% of `price_moved` cancels, and 59 of 108 historical fills were posted under 200 shares ahead).
2. **Is one control run enough, or does the comparison need to span the same market set?**
   - *Resolution*: Use a frozen snapshot of `runtime/markets.json` passed via `--markets-path` to ensure both treatment and control arms evaluate the exact same candidate universe, reporting market counts and median queue side-by-side.
3. **What queue count makes a run decidable?**
   - *Resolution*: Aim for ~1,600 quotes per arm where feasible; if constrained, record the exact quote count reached and apply the pre-registered decision rules accordingly.

## Improvement Proposal (Evidence-Based)
- **Pre-registered explicit decision rules**:
  - **Adopt**: Treatment arm reaches statistical power threshold, demonstrates higher fill rate than control, increases median lifetime without pair-cost re-gate violations.
  - **Reject**: Both arms reach target volume and treatment shows no fill rate improvement (e.g. zero fills in both arms, or identical churn).
  - **Inconclusive**: Power threshold not reached or market conditions diverge.
  - *Evidence*: `docs/runs/2026-09-30-run08-aged-out-rescue.md` and Issue #360 note that previous levers suffered from ambiguous post-hoc interpretations. Pre-registering the rules removes bias.

## Dependency Graph
```
[Task 1: Setup & Preconditions]
         │
         ▼
[Task 2: Paired Shadow Rehearsal Execution]
         │
         ▼
[Task 3: Metrics Extraction & Run Memo in docs/runs/]
```

## Task Breakdown

### Task 1: Setup, Frozen Universe Snapshot & Preconditions
- **Task ID**: `TASK-01` [COMPLETED]
- **Size**: XS
- **Domain Tag**: `[Backend/Logic]`
- **Target Files**: `runtime/scratch/`, `data/`
- **Helper Skill**: `test-driven-development`
- **Depends on**: None
- **Description**:
  1. Confirm targeted tests pass cleanly (`tests/test_trader_loop.py`, `tests/test_completable_pair_gate.py`, `tests/test_statistics_report.py`).
  2. Snapshot `runtime/markets.json` to a scratch path (`data/scratch_markets_frozen_360.json`), verifying and recording its SHA-256 hash (`8A4FED3EE54604E9000EC5E938A881534110AFC3CF06277CD0715E4DE738C009`).
  3. Ensure distinct scratch database paths exist for treatment (`data/360_shadow_qhold_treatment.db`) and control (`data/360_shadow_qhold_control.db`).
- **Verification**: Run targeted tests; verify frozen markets file exists and hash is computed.

### Task 2: Paired Rehearsal Execution & Telemetry Capture
- **Task ID**: `TASK-02` [COMPLETED]
- **Size**: M
- **Domain Tag**: `[Research]`
- **Target Files**: `data/360_shadow_qhold_treatment.db`, `data/360_shadow_qhold_control.db`
- **Helper Skill**: `research`
- **Depends on**: `TASK-01`
- **Description**:
  1. Execute treatment arm: `HUNTER_REQUOTE_HOLD_QUEUE=200` with `--db data/360_shadow_qhold_treatment.db --run-id shadow-qhold-200 --markets-path data/scratch_markets_frozen_360.json`.
  2. Execute control arm: `HUNTER_REQUOTE_HOLD_QUEUE=0` with `--db data/360_shadow_qhold_control.db --run-id shadow-qhold-0 --markets-path data/scratch_markets_frozen_360.json`.
  3. Monitor execution, orders, and quote counts in both databases.
  4. Ensure both processes exit cleanly without writing to production `data/orders.db`.
- **Verification**: Query scratch DBs for quote counts, order states, and ensure zero writes to `data/orders.db`.

### Task 3: Metrics Extraction, Memo Creation & Clean Closeout
- **Task ID**: `TASK-03` [COMPLETED]
- **Size**: S
- **Domain Tag**: `[Docs]`
- **Target Files**: `docs/runs/2026-10-04-queue-hold-200-rehearsal.md`
- **Helper Skill**: `documentation-and-adrs`
- **Depends on**: `TASK-02`
- **Description**:
  1. Generate statistics reports for both arms using `write_statistics_report` and `core_brain.kpi`.
  2. Extract side-by-side metrics: quote counts, distinct orders, median queue multiple, cancelled median lifetime, filled median lifetime, lifetime unknown count, cancel-reason mix (`price_moved` share), and fill rates.
  3. Write `docs/runs/2026-10-04-queue-hold-200-rehearsal.md` matching the standard structure from `docs/runs/2026-09-30-run08-aged-out-rescue.md`.
  4. Evaluate results against pre-registered rules and formulate explicit verdict (adopt / reject / inconclusive).
  5. Verify no changes were made to `core_brain/config.py` defaults.
- **Verification**: Verify memo file exists and contains all required tables and verdict; run targeted test suite.

## Checkpoint Stops
- **Checkpoint 1 (after TASK-01)**: Preconditions confirmed green, snapshot created.
- **Checkpoint 2 (after TASK-02)**: Paired runs finished, data gathered in scratch stores.
- **Checkpoint 3 (after TASK-03)**: Memo written in `docs/runs/`, diff verified, ready for Station IV review.

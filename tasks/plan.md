# Plan — #416: Widen activity-gate tolerances and scope gates to the dual-resting phase

Branch: i416/widen-activity-gate-tolerances-scope-gates | Issue: #416

- Tier: **Standard** — 2–5 files, internal module changes, one architectural decision (hedge bypass reads the registry lifecycle state via the existing seam).
- Task type: **Code** — threshold tuning + placement-gate scoping + regression tests.
- Stack: Python, SQLite registry, pytest; no external dependency.
- CodeRabbit plan: **arrived late** — the owner's `@coderabbitai plan` prompt (04:51Z) had no reply at Station II time; the reply landed at 05:02Z and was updated 05:06Z. Intake recorded after the build (reconciliation pass, 2026-10-08):
  - **ADOPTED (already in this plan):** Design Choice 2 — the placement bypass is verified against registry lifecycle state, never an inferred flag or reason text (`_admit_placements(lifecycle_state=...)` reading the state threaded by `lifecycle_context_for`). Adopted in the narrower form the plan chose: the state comes from the same registry read the visit already made, not a second `get_lifecycle_state` call — the escalated path narrows quoting to one pair-scoped intent (`quotes._decide_lifecycle_escalation` via `LifecycleQuoteOverride`), so a market-scoped state cannot leak a bypass to another pair's ordinary quotes.
  - **ADOPTED (already in this plan):** Design Choice 3 — an active-hedge market keeps its resting hedge. Implemented as the `_cancel_dropped_markets` shield (skip `open` cancels for `ESCALATED_HEDGE`/`HARD_STOP` cids, resolved still exits first, unreadable registry fails PROTECTED) rather than refresh-list retention; both stop the irreversible cancel, and the shield cannot accidentally keep a resolved market alive.
  - **REJECTED — Design Choice 1, "keep the selector defaults (prove 5c–8c already passes)".** Its premise was "no checked-in run records a sports market rejected for activity with its measured values." That evidence now exists and points the other way: the live universe written by the running screener (`runtime/market_universe.json`, 94 rows) shows the widened bar in force (`no movement: $0 traded in last 30m under $200`, `flat range: … 0.00c in last 30m < 1.00c`) while admitting one market at `movement_usd=463.45, range_cents=1.0` — a real sports/politics book the old `$500 / 2.0c` pair refused on both gates. CodeRabbit's own reading was right that a 5c–8c swing clears a 2.0c floor; the refusals were happening at the `$0–$8 / 0.0–0.1c` end and at `1c`-range books, which is what this plan widened. Recorded as `[VERIFIED]` on 2026-10-08 from the live universe file.
  - **REJECTED — per-order authorization keyed to (pair, condition_id, hedge token).** Not needed: on the escalated path the visit's plan is built from the pair-scoped override, so no ordinary passive intent for another pair can ride along in the same batch. Covered instead by `test_dual_resting_and_patient_states_get_no_bypass` (no state outside the two protected ones gets a bypass).
  - **REJECTED — "still measure queue-ahead and reachable flow while bypassing".** The bypass deliberately skips the tape read: a measurement that cannot change the answer is a wasted venue round-trip, and the admission reason carries the bypass and the state on the record.
  - **DEFERRED — Task 2.3, a regression test that `HARD_STOP` selling never touches activity gates (spy on `_admit_placements`).** Not in this plan's T3 scope; the sell path runs from the poll/`single_buy_saver` side which never calls `_admit_placements`. Carried as a follow-up candidate, not adopted here.
  - **NOT-APPLICABLE / noted:** CodeRabbit's `[UNVERIFIED]` markers for test counts (11 velocity / 17 movement) and its line-number corrections were already resolved from source in Station II.
- Build + verification evidence (2026-10-08): 5 focused suites green — `tests/test_velocity_gate.py tests/test_movement_gate.py` (36), `tests/test_queue_clear_gate.py tests/test_cancel_attribution.py` (95), `tests/test_trader_loop.py` (112); RED-first re-run with `scoring/config.py`, `core_brain/trader_loop.py`, `core_brain/single_leg_lifecycle.py` reverted to `0396b22` fails exactly the 7 new tests (1 velocity config-pin, 2 movement, 3 queue-clear bypass, 1 hedge shield) and passes again on restore. 5-minute `shadow_run` to `data/416_shadow_20261008_check.db` exit 0 (`rotations_returned=5 quoted=4 declined=0 errors=0`).
- Open questions resolved from code (Station II):
  - Q1 selection-vs-placement → BOTH, narrowly: `filter_markets.py` has no position awareness, so a selection reject drops the market and `_cancel_dropped_markets` cancels its `open` hedge orders (partials only WARNED) — a selection reject CAN strand a hedge. Preserve lifecycle-protected markets through refresh cleanup AND bypass runtime placement gates for their hedge orders. Never reinterpret selection gates as order-cancellation gates.
  - Q2 thresholds → keep existing selector dimensions, tune only measured thresholds: `select_min_movement_usd` 500.0 → 200.0, `select_min_range_cents` 2.0 → 1.0, with documented rationale. No `VELOCITY_GATE_TOLERANCE` / `MOVEMENT_GATE_THRESHOLD` constants exist (issue text already admits this).
- Improvement proposal (adopted, simplification): reuse the existing `lifecycle_context_for` seam (`trader_loop.py:1602`, `_lifecycle_quote_context`) to thread lifecycle state into the placement decision instead of adding a second registry lookup inside `_admit_placements`. `_cancel_dropped_markets` reads `get_lifecycle_state` per pair only on the rare dropped-market path.
- Safety: do not open or rewrite `data/orders.db`; tests use temporary DBs. No live quoting, Trader loop, manual completion, or dashboard START. Shadow rehearsal is the only permitted order-loop validation.

## Locked behavior

- Ordinary 5c–8c live-sports point swings (modeled fixture: ~$275/30m notional, ~6c range) pass both selector gates; multi-hour stalls ($0 / 0c) are still refused with wide margin.
- Placement bypass fires ONLY on registry lifecycle state `ESCALATED_HEDGE` / `HARD_STOP`, never an inferred flag; dual-resting markets keep full gate behavior.
- Unmeasured tape (`None`) still fails open everywhere; tape-read failure still fails open with the reason recorded, never silent.
- Pair-cost ceilings, gate telemetry, venue-state / cancel / reconcile / sell-risk paths: unchanged (explicit out of scope).

## Dependency graph

- T1 → T2
- T1 + T2 → T3

## Tasks

### T1 [x] — Widen selector tolerances with rationale [Backend/Logic] (S)

- Target files: `scoring/config.py`, `tests/test_movement_gate.py` (default assertion 500.0 → 200.0)
- Build: `select_min_movement_usd` 500.0 → 200.0, `select_min_range_cents` 2.0 → 1.0, with comment rationale (sports point-swing notional vs stall margin; range+notional gates are ANDed so a 1c flicker still needs real notional). Env overrides (`HUNTER_MIN_MOVEMENT_USD`, `HUNTER_MIN_RANGE_CENTS`) unchanged.
- Helper skill: `test-driven-development`
- Depends on: none.
- Verify: `python -m pytest -q tests/test_velocity_gate.py tests/test_movement_gate.py`; stall fixtures still refused, unmeasured tape still fail-open.

### T2 [x] — Scope gates to dual-resting; protect the hedge [Backend/Logic] (M)

- Target files: `core_brain/trader_loop.py`, `core_brain/single_leg_lifecycle.py` (one optional `lifecycle_state` field on `LifecycleQuoteContext`)
- Build: `_admit_placements` gains optional `lifecycle_state`; `ESCALATED_HEDGE`/`HARD_STOP` admits all passive placements with the bypass on the record and no tape read; `_visit_one` threads the state from `ev.lifecycle_context` (override state, hard-stop refusal carries `HARD_STOP`); `_cancel_dropped_markets` skips `open` cancels for cids whose pair rows read `ESCALATED_HEDGE`/`HARD_STOP` (resolved markets still exit first; read failure fails PROTECTED with a WARNED row).
- Helper skill: `test-driven-development`, `api-and-interface-design`
- Depends on: T1.
- Verify: `python -m pytest -q tests/test_queue_clear_gate.py tests/test_cancel_attribution.py tests/test_trader_loop.py`; dual-resting refusal unchanged, crossed legs still ungated, bypass reads the tape zero times.

### T3 [x] — Regression tests for normal-swing tolerance and active-hedge bypass [Backend/Logic] (S)

- Target files: `tests/test_velocity_gate.py`, `tests/test_movement_gate.py`, `tests/test_queue_clear_gate.py`, `tests/test_cancel_attribution.py`
- Build: normal-swing fixtures (5c–8c, modest notional) pass both gates; escalated-bypass cases (enforced gate + `ESCALATED_HEDGE`/`HARD_STOP` → admitted, reason names the state, tape unread); edge cases (unmeasured fail-open, dual-resting still gated, resolved still exits, protected-skip row contents).
- Helper skill: `test-driven-development`
- Depends on: T1, T2.
- Verify: focused suites green; 5-min `shadow_run` rehearsal exit 0 to its own DB as hands-on evidence.

## Checkpoints

- C1 after T1: thresholds demonstrable (normal-swing fixture passes, stall still refused).
- C2 after T2: hedge path demonstrable (escalated placement admitted, dropped-market hedge preserved).

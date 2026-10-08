# Plan — #416: Widen activity-gate tolerances and scope gates to the dual-resting phase

Branch: i416/widen-activity-gate-tolerances-scope-gates | Issue: #416

- Tier: **Standard** — 2–5 files, internal module changes, one architectural decision (hedge bypass reads the registry lifecycle state via the existing seam).
- Task type: **Code** — threshold tuning + placement-gate scoping + regression tests.
- Stack: Python, SQLite registry, pytest; no external dependency.
- CodeRabbit plan: none — only the owner `@coderabbitai plan` prompt comment exists on #416; nothing adopted, nothing to reject, nothing adopted as [UNVERIFIED].
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

### T3 [ ] — Regression tests for normal-swing tolerance and active-hedge bypass [Backend/Logic] (S)

- Target files: `tests/test_velocity_gate.py`, `tests/test_movement_gate.py`, `tests/test_queue_clear_gate.py`, `tests/test_cancel_attribution.py`
- Build: normal-swing fixtures (5c–8c, modest notional) pass both gates; escalated-bypass cases (enforced gate + `ESCALATED_HEDGE`/`HARD_STOP` → admitted, reason names the state, tape unread); edge cases (unmeasured fail-open, dual-resting still gated, resolved still exits, protected-skip row contents).
- Helper skill: `test-driven-development`
- Depends on: T1, T2.
- Verify: focused suites green; 5-min `shadow_run` rehearsal exit 0 to its own DB as hands-on evidence.

## Checkpoints

- C1 after T1: thresholds demonstrable (normal-swing fixture passes, stall still refused).
- C2 after T2: hedge path demonstrable (escalated placement admitted, dropped-market hedge preserved).

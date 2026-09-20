# Plan: Issue #240 — End-of-window proximity gate

Size: **Standard** (5 modules + tests, one architectural decision: refuse-vs-deepen default). Task type: **Code + Security** (risk-tightening gate).
Stack: Python, pytest. Live-money safety rules apply (no opening commands; read-only verification).

## Tasks

### T1 [Backend/Logic] — Cadence helper in `order_registry.py`
Read-only query over recent `cycle_intent` rows; median gap between consecutive distinct `cycle` timestamps; `None` when too few rows. Follow `registry_committed_usd` aggregate style.
- Files: `core_brain/order_registry.py`, `tests/test_endgame_gate.py` (new)
- Verify: pytest cadence test (known timestamps → expected value; sparse rows → `None`).

### T2 [Backend/Logic] — Gate config knobs in `config.py`
`MakerConfig` fields next to `quote_window_frac`/`enforce_quote_window`: `enforce_endgame_gate=False`, `endgame_horizon_min`, `endgame_max_cadence_sec`, `endgame_action="refuse"`, `endgame_deepen_offset`, `observed_cadence_sec=None`. `HUNTER_*` overrides via `_bounded_float` (numerics) + `enable_pairs_rule` idiom (bool/string). `ValueError` naming the env var on bad values. Comment block pointing to the loss post-mortem.
- Files: `core_brain/config.py`, `tests/test_endgame_gate.py`
- Verify: pytest config defaults/overrides/validation tests.

### T3 [Backend/Logic] — Real timing in `evaluate_market_quote`
Compute `t_remaining` from the fetched `market` object (drop fake `1e9`); `window_frac` only for the 5-min series, else `None`. Callers unchanged.
- Files: `core_brain/quotes.py`
- Verify: pytest (regression test in T6 covers it; existing quote tests green).

### T4 [Backend/Logic] — Per-cycle cadence attach in `trader_loop.py`
Call T1 helper once per rotation; merge onto per-market config in `_market_cfg` (same pattern as `fleet_posture`).
- Files: `core_brain/trader_loop.py`
- Verify: pytest trader-loop tests green + new test asserting the value lands on the config.

### T5 [Backend/Logic] — Gate rule in `_decide_quotes_from_mid`
Per-side loop near the `strict_paired_inventory` block. Fires iff enabled + `t_remaining/60 ≤ horizon` + cadence known and above budget. Light-side balancing (`risk.naked_side`) always allowed; new-exposure sides (`inv.avg(other_side) <= 0`) refused or deepened via existing offset pipeline + clamp. `why` reason carries `endgame_gate` **plus the measured numbers** (minutes-to-resolution, cadence) so shadow runs can count firings.
- Files: `core_brain/quotes.py`
- Verify: pytest regression tests (T6).

### T6 [Backend/Logic] — S&P 2026-09-18 regression tests
Flat `Inventory()`, hand-built books, `t_remaining≈32min`, cadence `≈210s`: gate-on refuses/deepens, gate-off posts (fails without change). Second case: unhedged inventory → balancing quote allowed.
- Files: `tests/test_endgame_gate.py`
- Verify: pytest new tests green; full `python -m pytest -q` green (agent-run).

## Improvement proposal (adopted by default)
The `why` reason will include the measured minutes-to-resolution and cadence values, not just the `endgame_gate` tag — otherwise shadow runs can see the gate fired but cannot measure its firing rate (issue Phase 3 Task 4 demands measurable shadow visibility).

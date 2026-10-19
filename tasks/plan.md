# Implementation Plan: Purge Broken Past Tournament Data, Assumptions, and Misleading Winner Blueprints
Branch: `i698/purge-tournament-blueprints-and-data` | Issue: `#698` (absorbing `#693`)

## Problem Statement & Scope
Historical shadow tournament runs (#484/#485) ran on flawed simulation logic (phantom exit fills, positional attribution). `scripts/tournament_cockpit_server.py` carried obsolete claims of #484 "Empirical Winner Synthesis", outdated `HUNTER_*` knobs, and a `--interval 5.0` default in `scripts/shadow_tournament.py` that tripped runtime clamping warnings against `MAX_ROTATION_INTERVAL_SEC = 1.0`. Furthermore, stale docstrings and comments referencing deleted knobs remain in `scripts/grace_sweep_report.py` and `core_brain/statistical_analytics.py` (Issue #693).

This plan cleanses tournament blueprints and defaults, harmonizing the suite with canonical `BOT_*` names and modern 1.0s cycle cadence.

---

## CodeRabbit Plan Alignment
- Adopted CodeRabbit's 3-task structure:
  1. Tournament launcher interval default to `MAX_ROTATION_INTERVAL_SEC` and unit tests.
  2. Blueprint table and `.env` block modernization with canonical `BOT_*` keys and HTML section assertions.
  3. Docstring cleanup for #693 (`grace_sweep_report.py`, `statistical_analytics.py`) and verification checks.
- Safety boundaries preserved: `BOT_POS_PRICE_MAX_PAIR_COST_USD` in `.env` is comment-only (`#`), dollar per share units fixed (`0.08 (8.0¢)`), and no production engine files touched.

---

## Task Decomposition

### Task 1: Launcher Interval Harmonization (`scripts/shadow_tournament.py`)
- Import `MAX_ROTATION_INTERVAL_SEC` from `core_brain.config` into `scripts/shadow_tournament.py`.
- Update `build_tournament_plan()` default parameter: `interval: float = MAX_ROTATION_INTERVAL_SEC`.
- Update CLI arg parser default: `parser.add_argument("--interval", type=float, default=MAX_ROTATION_INTERVAL_SEC, help="Cycle rotation interval in seconds (default: 1.0)")`.
- Add focused tests in `tests/test_shadow_tournament.py`:
  - Default plan interval equals `1.0` and arm arguments use `"1.0"`.
  - Dry run `main(["--dry-run"])` emits JSON with `"interval": 1.0`.
  - Generated worker args parse cleanly through `shadow_run._parse_args` with zero warnings from `core_brain.config`.
  - Explicit intervals (`0.25` clean, `5.0` clamped to 1.0 with single warning).

### Task 2: Blueprint Modernization & Docstring Cleanup (`scripts/tournament_cockpit_server.py`, `scripts/grace_sweep_report.py`, `core_brain/statistical_analytics.py`)
- Edit `scripts/tournament_cockpit_server.py` HTML blueprint tab:
  - Remove "Pricing Offset" row (`HUNTER_REWARD_OFFSET`).
  - Update rows to canonical `BOT_*` keys: `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS` (6.0¢), `BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON=0` (Disabled), `BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES` (500 shares), `BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS` (`0.08 (8.0¢)`).
  - Replace Single-Buy Stop Cap with `BOT_RISK_STOP_MODE=ABSOLUTE_DROP` and `BOT_RISK_STOP_ADVERSE_DROP_CENTS=0.045` (`$0.045 (4.5¢)`).
  - Replace Single-Buy Grace Window with Aged-Out Rescue Lead: `BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC=45.0` (`45.0s`).
  - Update Absolute Pair Cap to `BOT_POS_PRICE_MAX_PAIR_COST_USD` (`$0.990`).
  - Update rehearsal command to `--interval 1.0`.
  - Modernize `.env` snippet with canonical `BOT_*` variables and comment-only pair cap.
- Reword docstring in `scripts/grace_sweep_report.py:3` (governed by `pairs_exit_window_sec` / `aged_out_rescue_lead_sec`).
- Reword comment in `core_brain/statistical_analytics.py:119-124` (remove dead `stop_loss_trigger_usd_cents = 4.0` reference).
- Add test in `tests/test_shadow_tournament.py` asserting blueprint tab correctness.

### Task 3: Regression Suite Verification & Operator Verification Guide
- Verify all targeted test suites pass.
- Produce operator hands-on verification instructions.

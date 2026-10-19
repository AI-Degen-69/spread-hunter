# Quality Guardrails & Constraints — Issue #698 (absorbing #693)
Branch: `i698/purge-tournament-blueprints-and-data` | Issue: `#698` (absorbing `#693`)

## 1. Zero Regressions & Verification Gates
- **Targeted Test Suites:**
  - `python -m pytest -q tests/test_shadow_tournament.py`
  - `python -m pytest -q tests/test_rotation_interval_cap.py`
  - `python -m pytest -q tests/test_tournament_cockpit_funnel.py`
- **Full Suite in CI:** Local verification runs only targeted tests matching touched files. Full suite is the GitHub CI merge gate.

## 2. Invariants & Scope Bounds
- **Strict Boundary:** Do NOT touch live production engine files (`core_brain/trader_loop.py`, `core_brain/order_manager.py`).
- **No Venue Interactions:** Do not launch live runs, workers, or place orders during tests or verification.
- **Single Source for Cadence:** `scripts/shadow_tournament.py` must import `MAX_ROTATION_INTERVAL_SEC` from `core_brain.config`, not duplicate `1.0`.
- **Environment & Copy Purity:**
  - Zero `HUNTER_*` names in the cockpit Blueprint tab or `.env` copy.
  - Zero retired knobs (`HUNTER_REWARD_OFFSET`, `HUNTER_STOP_LOSS_TRIGGER_USD_CENTS`, `HUNTER_SINGLE_BUY_GRACE_SEC`).
  - `BOT_POS_PRICE_MAX_PAIR_COST_USD` in the `.env` snippet must only appear commented out (`#`) to prevent accidental live override.
- **Cadence Harmonization:**
  - Tournament plan and CLI default `--interval` must be `1.0`s (matching `MAX_ROTATION_INTERVAL_SEC`), eliminating runtime clamping warnings.

## 3. Anti-Cheat & Quality Rules
- **No Disabling Tests:** No test skipping or muted assertions.
- **Operator Verification Rule:** Never ask or suggest the operator to run `pytest`. Operator verification must be 100% hands-on surface behavior (CLI `--dry-run`, browser inspection, $0 risk).

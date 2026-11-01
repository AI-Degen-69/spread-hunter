# CONSTRAINTS.md — Issue #917: Multi-Strategy Tournament Design Addressing Live Market Adverse Selection

Branch: `plan_github_issue_917` | Issue: #917

Locked during Station II planning. Stations III–V must hold every line below.

## Zero Regressions

- Targeted test suites covering all touched files must pass:
  - `python -m pytest -q tests/test_tournament_arms_i917.py tests/test_shadow_tournament.py tests/test_config_divergence_guard.py tests/test_config_single_source.py tests/test_wide_book_trial.py tests/test_trader_loop.py`
- Existing config single-source and divergence guards (`tests/test_config_divergence_guard.py`, `tests/test_config_single_source.py`, `tests/test_wide_book_trial.py`) must pass without modifying any shipped field default on `MakerConfig` or `SCORING_DEFAULTS`.
- In `core_brain/trader_loop.py`, `_market_specs` must preserve the exact 17-key dictionary shape asserted by `tests/test_trader_loop.py`.

## Scope Fences (Hard)

- **Only these files may be created or modified:**
  - `core_brain/config.py` (strictly limited to parsing `BOT_RISK_STRANDED_COMPLETION_ON`, `BOT_RISK_STRANDED_MAX_WAIT_SEC`, and `BOT_QUOTE_BOOK_MAX_SPREAD_USD` inside `MakerConfig.from_env()`)
  - `core_brain/trader_loop.py` (strictly limited to filtering sports/live rows in `_market_specs` when `BOT_MARKET_EXCLUDE_SPORTS_ON` or `FILTER_EXCLUDE_SPORTS` is enabled)
  - `scripts/shadow_tournament.py` (removal of retired per-arm dashboard ports and args)
  - `scripts/tournament_cockpit_server.py` (wiring tournament 917 into unified cockpit)
  - `configs/tournaments/i917-multistrat-defense_arms.json`
  - `docs/runs/2026-11-01-917-multistrat-defense.md`
  - `tests/test_tournament_arms_i917.py`, `tests/test_shadow_tournament.py`, `tests/test_tournament_cockpit_arms.py`, `tests/test_menu_tournament_reconcile.py`
  - `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`
- **Must NOT be modified:**
  - Shipped default values in `core_brain/config.py` and `scoring/config.py`
  - `core_brain/single_buy_saver.py`, `core_brain/single_leg_lifecycle.py`, `scoring/selector.py`, `scripts/reevaluate_tournament_run.py`
  - `data/orders.db`, `data/tournaments/i799*`, `data/tournaments/i905*`

## Anti-Cheat & Safety Rules

- Never skip or disable tests, delete existing assertions, or suppress linters.
- Never run live-order commands (`quote`, `complete`, `trader_loop`, or starting the live Trader).
- No new external dependencies.

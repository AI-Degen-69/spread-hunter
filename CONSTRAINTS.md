# CONSTRAINTS.md — Issue #842: Auto-close profitable orphaned positions after duration threshold

Branch: `plan_issue_842` | Issue: #842

Locked during Station II planning. Stations III–V must hold every line below.
Tighten only with operator approval; never loosen silently.

## Zero regressions

- Focused test suites covering touched modules must pass locally:
  `python -m pytest tests/test_single_leg_lifecycle.py tests/test_single_buy_saver.py tests/test_close_method_taxonomy.py tests/test_config_single_source.py tests/test_config_divergence_guard.py -q`
- The full `python -m pytest -q` regression suite runs in GitHub CI and is the merge gate.
- Existing hard-stop protection, aged-out settlement rescue, and paired-merge resolution must remain 100% behavior-preserving.
- Hard stop precedence is preserved: a hard stop takes precedence over taking profit; taking profit takes precedence over aged-out rescue.

## Scope fences (hard)

- **Permitted file changes:**
  - `core_brain/config.py`: configuration fields, parsing in `load_config`, bounds validation
  - `.env.example`: documentation of the three new knobs
  - `core_brain/close_methods.py`: canonical `ORPHAN_PROFIT_EXIT = "orphan_profit_exit"` constant and entry in `RESCUE_METHODS`
  - `dashboard/static/app.js`: update pinned `EXIT_METHODS` Set to include `"orphan_profit_exit"`
  - `core_brain/single_leg_lifecycle.py`: pure `OrphanProfitVerdict`, floor calculator, and `evaluate_orphan_profit` pure verdict function
  - `core_brain/single_buy_saver.py`: `min_profit_pct` handling and post-cancel guards in `exit_single_buy`, and orphan profit evaluation branch in `_manage_single_leg_positions_locked`
  - Unit tests in `tests/test_single_leg_lifecycle.py`, `tests/test_single_buy_saver.py`, `tests/test_close_method_taxonomy.py`, and `tests/test_config_single_source.py`.
- **Files explicitly frozen / not to modify:**
  - `scoring/config.py`: must stay unmodified; it inherits all fields from `core_brain.config.MakerConfig`.
  - `core_brain/market_resolution.py` and paired shadow/merge resolution.
  - `core_brain/pair_scanner.py`, `core_brain/trader_loop.py`, `core_brain/order_manager.py`.
  - `core_brain/lifecycle_reset.py`: `reset_condition` logic stays untouched.
- **Never call `mark_hard_stop_block` on orphan profit exits:**
  - An orphan taking profit is a winning or capital-freeing exit, not an adverse stop-out; do not penalize the market with a hard-stop lockout.
- **No writes to any production `.db`:**
  - Tests must use `tmp_path`. Production `data/orders.db` is strictly untouched.
  - No live venue-reaching commands (`quote`, `complete`, Trader loop, or dashboard START).

## Anti-cheat

- Never skip, xfail, or delete existing tests to get green.
- Never weaken assertions or mock around broken contracts.
- Gross price profitability check: lowest tick price at least `average_cost * (1 + pct)` and strictly greater than `average_cost`.
- Double-sided positions must be strictly rejected (`opposite_leg_filled`).
- Re-check after cancellation: if cancellation reveals an opposite fill (`venue_light_matched > 0` or opposite shares present in registry), hold without selling (`opposite_filled`). If the bid dropped below floor after cancel, hold without selling (`profit_gone`).

## Architecture & Simplicity

- Minimal, boring implementation: no new classes or complex state machines.
- Pure decision logic in `core_brain/single_leg_lifecycle.py` decoupled from venue execution.
- Execution safely delegated through existing atomic `exit_single_buy` primitive.
- Condition reset uses existing `reset_condition` with reason `"orphan_profit_exited"`.

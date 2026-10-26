# Implementation Plan — Issue #842: Auto-close profitable orphaned positions after duration threshold

Branch: `plan_issue_842` | Issue: #842

## Summary

When only one leg of a binary pair fills and counter-buy orders remain unfilled past the entry window without reaching resolution, a position can sit as an unhedged directional bet for hours. When market prices move favorably, these orphaned legs often build significant unrealized profits (+5% to +18%+). 
This feature adds an automated exit mechanism that detects orphaned single-leg positions held beyond a configurable age threshold (`bot_risk_orphan_profit_min_age_sec`, default 1 hour / 3600s), verifies that the executable best bid meets or exceeds a target gross profit threshold (`bot_risk_orphan_min_profit_pct`, default 0.05 / 5%), cancels resting counter-orders, executes a guarded market sell at or above the profit floor, books the close as `orphan_profit_exit`, and resets the market condition without triggering a punitive hard-stop lockout.

---

## Step 0A: CodeRabbit Plan Intake & Analysis

- **Adopted:**
  - Config knobs: `bot_risk_gate_orphan_profit_close_on` (bool, default `False`), `bot_risk_orphan_profit_min_age_sec` (float, default `3600.0`, bounds `1.0..86400.0`), `bot_risk_orphan_min_profit_pct` (float, default `0.05`, bounds `0.0..1.0`).
  - Canonical close method `ORPHAN_PROFIT_EXIT = "orphan_profit_exit"` added to `RESCUE_METHODS` in `core_brain/close_methods.py`. Netting in `order_registry.py` already selects `RESCUE_METHODS`, so inventory deduction works automatically.
  - Pinned `EXIT_METHODS` update in `dashboard/static/app.js` to stay in sync with `DASHBOARD_FORCED_EXIT_METHODS`.
  - Pure verdict in `core_brain/single_leg_lifecycle.py`: `OrphanProfitVerdict` dataclass and `evaluate_orphan_profit(...)` pure helper.
  - Guarded execution in `exit_single_buy`: parameter `min_profit_pct`, rechecking opposite fills and profit floor post-cancel, setting limit price to `max(slippage_floor, profit_floor)`.
  - Manager integration in `_manage_single_leg_positions_locked`: placing the orphan-profit branch immediately after hard-stop and before aged-out rescue, ensuring hard-stop priority is preserved.
  - Reset behavior: calling `reset_condition(..., reason="orphan_profit_exited")` without calling `mark_hard_stop_block`.
- **Rejected:**
  - Net profit after fees model: Fees on live taker sales are unknown at sell time (and cost basis excludes entry fees). Gross profit over cost basis is the robust, consistent standard.
  - Extra dashboard status cards: Not requested in issue scope.
  - Optional `fresh` parameter in `core_brain/shadow_exec.py`: Shadow ladder walk already guarantees execution at or above the submitted limit.
- **Verified:**
  - All file paths and seam symbols (`single_buy_saver.py`, `single_leg_lifecycle.py`, `close_methods.py`, `config.py`) confirmed live in the repository.
  - `scoring/config.py` inherits from `core_brain.config.MakerConfig` and requires zero edits.

---

## Step 5: Committed Enhancement

- **Edge-Case Hardening (Post-Cancel Verification):**
  - *Evidence:* In `core_brain/single_buy_saver.py` lines 1124-1144, `_cancel_orders` cancels working orders on both legs before reading venue state via `_venue_extra`. In fast-moving markets, a resting counter-order on the light leg may fill right as cancellation is initiated.
  - *Enhancement:* When `min_profit_pct` is set in `exit_single_buy`, we explicitly verify after cancellation that `after["legs"][light_token]["matched"] == 0` and `venue_light_matched == 0`. If an opposite fill is detected, the function aborts the sell with `action: "orphan_profit_hold", reason: "opposite_filled"`. Furthermore, if the fresh book bid has dropped below the calculated profit floor, it holds with `action: "orphan_profit_hold", reason: "profit_gone"`.
  - *Verification:* Tested via unit tests simulating late counter-fill and book drop after cancellation.

---

## Interface Contracts

### 1. `core_brain/config.py`
```python
bot_risk_gate_orphan_profit_close_on: bool = False
bot_risk_orphan_profit_min_age_sec: float = 3600.0  # seconds in 1.0..86400.0
bot_risk_orphan_min_profit_pct: float = 0.05         # fraction in 0.0..1.0
```

### 2. `core_brain/close_methods.py`
```python
ORPHAN_PROFIT_EXIT = "orphan_profit_exit"
# Included in METHODS with RESCUE_BUCKET, auto-populating RESCUE_METHODS
```

### 3. `core_brain/single_leg_lifecycle.py`
```python
@dataclass(frozen=True)
class OrphanProfitVerdict:
    eligible: bool
    reason: str
    held_token_id: Optional[str] = None
    held_size: float = 0.0
    average_cost: float = 0.0
    profit_floor: float = 0.0
    observed_bid: Optional[float] = None

def orphan_profit_floor(average_cost: float, min_profit_pct: float, tick_size: float = 0.01) -> float: ...

def evaluate_orphan_profit(
    position: SingleLegPosition,
    age_s: Optional[float],
    min_age_sec: float,
    min_profit_pct: float,
    tick_size: float = 0.01,
) -> OrphanProfitVerdict: ...
```

### 4. `core_brain/single_buy_saver.py`
```python
def exit_single_buy(
    client, registry, pair_id: str, bot_pos_price_max_pair_cost_usd: float,
    *, live: bool = True, venue_positions: Optional[dict[str, float]] = None,
    reason: str = "single_buy_exit", force: bool = False,
    trigger_bid: Optional[float] = None, method: str = SINGLE_BUY_EXIT,
    min_profit_pct: Optional[float] = None,
) -> dict: ...
```

---

## Tasks

### Task 1: Configuration, Close Method Taxonomy & Pure Verdict [Backend/Logic]
- **Size:** M
- **Target files:**
  - `core_brain/config.py`
  - `.env.example`
  - `core_brain/close_methods.py`
  - `dashboard/static/app.js`
  - `core_brain/single_leg_lifecycle.py`
  - `tests/test_single_leg_lifecycle.py`
  - `tests/test_close_method_taxonomy.py`
  - `tests/test_config_single_source.py`
- **What is built:**
  - Add `bot_risk_gate_orphan_profit_close_on`, `bot_risk_orphan_profit_min_age_sec`, and `bot_risk_orphan_min_profit_pct` to `MakerConfig` and `from_env()`.
  - Document knobs in `.env.example`.
  - Add `ORPHAN_PROFIT_EXIT = "orphan_profit_exit"` to `core_brain/close_methods.py` in `RESCUE_METHODS`.
  - Add `"orphan_profit_exit"` to `const EXIT_METHODS` in `dashboard/static/app.js`.
  - Implement `orphan_profit_floor` and `evaluate_orphan_profit` in `core_brain/single_leg_lifecycle.py`.
  - Add unit tests for configuration bounds, taxonomy sync, and verdict calculations (boundary tests, tick ceiling, flat, two-sided, age unknown/too young, below floor, eligible).
- **Depends on:** None
- **Verification:**
  - `python -m pytest tests/test_single_leg_lifecycle.py tests/test_close_method_taxonomy.py tests/test_config_single_source.py -q`

### Task 2: Guarded Execution in `exit_single_buy` [Backend/Logic]
- **Size:** S
- **Target files:**
  - `core_brain/single_buy_saver.py`
  - `tests/test_single_buy_saver.py`
- **What is built:**
  - Add `min_profit_pct: Optional[float] = None` to `exit_single_buy`.
  - When `min_profit_pct` is provided, after cancel and venue re-read:
    - Verify light leg has 0 matched shares in registry and `venue_light_matched <= SIZE_EPS`. If not, return `{"action": "orphan_profit_hold", "reason": "opposite_filled", ...}`.
    - Recompute held cost and profit floor; check if fresh `bid < profit_floor`. If so, return `{"action": "orphan_profit_hold", "reason": "profit_gone", ...}`.
    - Set `min_price = max(min_price, profit_floor)`.
    - Record close with `method=ORPHAN_PROFIT_EXIT`, `reason="orphan_profit_exit"`, `submitted_floor=min_price`.
- **Depends on:** Task 1
- **Verification:**
  - `python -m pytest tests/test_single_buy_saver.py -k "test_orphan_exit" -q`

### Task 3: Manager Routing & Reset in `_manage_single_leg_positions_locked` [Backend/Logic]
- **Size:** M
- **Target files:**
  - `core_brain/single_buy_saver.py`
  - `tests/test_single_buy_saver.py`
- **What is built:**
  - Insert orphan profit check in `_manage_single_leg_positions_locked` after hard stop and before aged-out rescue.
  - Check `cfg.bot_risk_gate_orphan_profit_close_on`.
  - Calculate `age_s = (now_s * 1000.0 - last_fill_ms) / 1000.0` if `last_fill_ms > 0` else `None`.
  - Call `evaluate_orphan_profit(position, age_s, cfg.bot_risk_orphan_profit_min_age_sec, cfg.bot_risk_orphan_min_profit_pct, grid_tick)`.
  - If eligible, persist lifecycle decision, call `exit_single_buy(..., force=True, reason="orphan_profit_exit", method=ORPHAN_PROFIT_EXIT, trigger_bid=held_bid, min_profit_pct=...)`.
  - On `exited`: decrement venue position, call `reset_condition(..., reason="orphan_profit_exited")` (never calling `mark_hard_stop_block`), update result.
  - On hold (`orphan_profit_hold`, `dust_hold`, `route_to_merge`), append result and keep managing.
  - Add comprehensive manager tests (toggle off, age boundary, bid below floor, two-sided skip, hard stop precedence, aged-out precedence, partial fills, repeat-sale prevention, refill reset).
- **Depends on:** Task 2
- **Verification:**
  - `python -m pytest tests/test_single_buy_saver.py -k "test_manager_orphan" -q`

### Task 4: Regression Suites and Divergence Checks [Backend/Logic]
- **Size:** S
- **Target files:**
  - `tests/`
- **What is built:**
  - Verify all related regression suites: taxonomy, single source config, config divergence guard, single buy saver, single leg lifecycle, lifecycle reset, hard stop lockout window.
- **Depends on:** Task 3
- **Verification:**
  - `python -m pytest tests/test_single_leg_lifecycle.py tests/test_single_buy_saver.py tests/test_close_method_taxonomy.py tests/test_config_single_source.py tests/test_config_divergence_guard.py -q`

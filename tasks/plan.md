# Implementation Plan: Single-Leg Trailing Peak Momentum Rider and Entry Price Ceiling

Branch: plan_github_issue_926 | Issue: #926

## 1. Environment & Sizing
- **Tech Stack:** Python 3.11+, pytest, SQLite (`OrderRegistry`), Polymarket CLOB integration
- **Size Tier:** Standard (4-5 files in `core_brain/`, unit tests)
- **Task Type:** Code (Strategy / Risk / Execution)

## 2. CodeRabbit Plan Intake
- **Status:** No plan comment posted by CodeRabbit on issue #926 at intake time.
- **Adopted:** Scope directly derived from issue #926 specification and verified repository seams.
- **Rejected:** None.
- **Unverified:** None. All target symbols and files verified verbatim against repository code.

## 3. Improvement Proposal (Evidence-Based Edge Case Hardening)
- **Verbatim Evidence:** `core_brain/single_leg_lifecycle.py:733-734`:
  ```python
  if decision.action == "refused" or decision.state is previous_state:
      return decision
  ```
- **Finding:** In `single_leg_lifecycle.evaluate()`, if a position is in `LegState.TRAILING_RIDER` and receives a new higher bid that raises `peak_bid`, `decision.state is previous_state` would evaluate to `True`. Without checking `peak_bid`, the function would return early without calling `persist_decision()`. Consequently, updated peak watermarks would fail to save to SQLite (`evidence_json`), and restarts or subsequent cycles would lose the watermark.
- **Proposal (Adopted by default):** Update the persistence condition to:
  `if decision.action == "refused" or (decision.state is previous_state and decision.peak_bid == previous_peak_bid): return decision`
  ensuring peak watermark advances are reliably persisted to the database.

## 4. Locked Interface Contracts
### 4.1 Configuration (`core_brain/config.py`)
- `MakerConfig.bot_risk_trailing_rider_on: bool = False` (ENV: `BOT_RISK_TRAILING_RIDER_ON`)
- `MakerConfig.bot_risk_trailing_rider_delta_usd: float = 0.02` (ENV: `BOT_RISK_TRAILING_RIDER_DELTA_USD`)
- `MakerConfig.bot_pos_price_max_entry_usd: float = 0.60` (ENV: `BOT_POS_PRICE_MAX_ENTRY_USD`)

### 4.2 Lifecycle Models & Transitions (`core_brain/single_leg_lifecycle.py`)
- Enum addition: `LegState.TRAILING_RIDER = "TRAILING_RIDER"`
- Dataclass addition: `LifecycleDecision.peak_bid: float | None = None`
- Function signatures:
  ```python
  def transition(
      position: SingleLegPosition,
      previous_state: LegState | None,
      *,
      bot_pos_price_max_pair_cost_usd: float,
      settlement_due: bool = False,
      bot_risk_stop_mode: str = "NONE",
      bot_risk_stop_adverse_drop_usd: float = 0.05,
      bot_risk_stop_max_drawdown_pct: float = 10.0,
      bot_risk_circuit_max_drawdown_pct: float = 75.0,
      escalation_multiple: float = 2.0,
      tick_size: float = 0.01,
      bot_risk_trailing_rider_on: bool = False,
      bot_risk_trailing_rider_delta_usd: float = 0.02,
      previous_peak_bid: float | None = None,
  ) -> LifecycleDecision
  ```

### 4.3 Quoting Filter (`core_brain/quotes.py`)
- In `_decide_quotes_from_mid`:
  Block any quote intent where `price > cfg.bot_pos_price_max_entry_usd` with reason:
  `{side}: price {price:.3f} > max entry {cfg.bot_pos_price_max_entry_usd:.3f}`.

### 4.4 Execution Wiring (`core_brain/single_buy_saver.py`, `core_brain/trader_loop.py`)
- In `single_buy_saver.py` (`_manage_single_leg_positions_locked`):
  - When in `trailing_rider`, cancel opposite resting orders via `_cancel_orders`.
  - When `trailing_rider_exit`, execute taker market exit via `exit_single_buy(..., reason="trailing_rider_exit", force=True, trigger_bid=held_bid)`.
- In `trader_loop.py`:
  - When `decision.action == "trailing_rider"`, cancel active pair orders.
  - When `decision.action == "trailing_rider_exit"`, halt quoting and return refusal context.

---

## 5. Dependency Graph
```
Task 1 (Config Knobs & Quote Price Ceiling)
   │
   ▼
Task 2 (Trailing Peak Rider Lifecycle State & Watermark Persistence)
   │
   ▼
Task 3 (Execution Engine Wiring & Order Cancellation)
   │
   ▼
Task 4 (Shadow Run Verification & Standalone Rehearsal)
```

---

## 6. Implementation Tasks

### Task 1: Configuration Knobs & Quote Price Ceiling
- **Task ID:** TASK-1
- **Size:** S (2 files)
- **Domain Tag:** `[Backend/Logic]`
- **Target Files:**
  - `core_brain/config.py`
  - `core_brain/quotes.py`
  - `tests/test_config.py`
  - `tests/test_quotes.py`
- **Description:**
  - Add `bot_risk_trailing_rider_on`, `bot_risk_trailing_rider_delta_usd`, and `bot_pos_price_max_entry_usd` to `MakerConfig`.
  - Parse environment variables `BOT_RISK_TRAILING_RIDER_ON`, `BOT_RISK_TRAILING_RIDER_DELTA_USD`, and `BOT_POS_PRICE_MAX_ENTRY_USD` in `from_env()`.
  - In `core_brain/quotes.py:quote_resting_price` / `_decide_quotes_from_mid`, block any maker bid where calculated price exceeds `bot_pos_price_max_entry_usd`.
- **Assigned Helper Skill:** `test-driven-development`
- **Depends on:** None
- **Verification Method:** `python -m pytest -q tests/test_config.py tests/test_quotes.py`

### Task 2: Trailing Peak Rider Pure State Transitions & Watermark Persistence
- **Task ID:** TASK-2
- **Size:** M (2 files)
- **Domain Tag:** `[Backend/Logic]`
- **Target Files:**
  - `core_brain/single_leg_lifecycle.py`
  - `tests/test_single_leg_lifecycle.py`
- **Description:**
  - Add `LegState.TRAILING_RIDER` enum and `peak_bid` field to `LifecycleDecision`.
  - In `transition()`:
    - If `bot_risk_trailing_rider_on` is True and `held_bid > held_average` (or `previous_state is LegState.TRAILING_RIDER`):
      - Compute `peak_bid = max(previous_peak_bid or held_average, held_bid)`.
      - Check trailing stop: if `held_bid <= peak_bid - bot_risk_trailing_rider_delta_usd`, return `action="trailing_rider_exit"`, `state=LegState.TRAILING_RIDER`.
      - Otherwise, return `action="trailing_rider"`, `state=LegState.TRAILING_RIDER`.
  - In `persist_decision()` and `evaluate()`:
    - Load and save `peak_bid` in `evidence_json`.
    - Persist when state changes OR when `peak_bid != previous_peak_bid`.
- **Assigned Helper Skill:** `test-driven-development`
- **Depends on:** TASK-1
- **Verification Method:** `python -m pytest -q tests/test_single_leg_lifecycle.py`

### Checkpoint 1 (Tasks 1-2 Complete)
- Verify config parsing, quote ceiling filtering, and pure lifecycle state transitions in isolation.

### Task 3: Execution Engine Wiring & Opposite Leg Cancellation
- **Task ID:** TASK-3
- **Size:** M (3 files)
- **Domain Tag:** `[Backend/Logic]`
- **Target Files:**
  - `core_brain/single_buy_saver.py`
  - `core_brain/trader_loop.py`
  - `tests/test_single_buy_saver.py`
  - `tests/test_trader_loop.py`
- **Description:**
  - In `core_brain/single_buy_saver.py` (`_manage_single_leg_positions_locked`):
    - Extract `previous_peak_bid` from `previous_record.evidence_json`.
    - Pass trailing rider knobs to `transition()`.
    - When `decision.action == "trailing_rider"`, cancel opposite resting orders via `_cancel_orders` so upside is uncapped.
    - When `decision.action == "trailing_rider_exit"`, call `exit_single_buy(..., reason="trailing_rider_exit", force=True, trigger_bid=held_bid)`.
  - In `core_brain/trader_loop.py`:
    - When `decision.action == "trailing_rider"`, cancel active pair orders so opposite leg cannot rest or fill.
    - When `decision.action == "trailing_rider_exit"`, halt quoting on the market and return refusal context.
- **Assigned Helper Skill:** `test-driven-development`
- **Depends on:** TASK-2
- **Verification Method:** `python -m pytest -q tests/test_single_buy_saver.py tests/test_trader_loop.py`

### Task 4: Shadow Run Support & Verification Rehearsal
- **Task ID:** TASK-4
- **Size:** S (2 files)
- **Domain Tag:** `[Backend/Logic]`
- **Target Files:**
  - `core_brain/shadow_run.py`
  - `tests/test_shadow_run.py`
- **Description:**
  - Wire trailing rider support into `shadow_run.py` via `BOT_RISK_TRAILING_RIDER_ON` (and optional CLI flag `--trailing-rider`).
  - Run full targeted test suite covering all touched modules.
  - Execute a standalone shadow run rehearsal with trailing rider enabled.
- **Assigned Helper Skill:** `incremental-implementation`
- **Depends on:** TASK-3
- **Verification Method:**
  - Automated: `python -m pytest -q tests/test_config.py tests/test_quotes.py tests/test_single_leg_lifecycle.py tests/test_single_buy_saver.py tests/test_shadow_run.py`
  - Hands-on rehearsal: `python -m core_brain.shadow_run --minutes 1 --db data/926_shadow_rehearsal.db`

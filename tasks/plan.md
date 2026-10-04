Branch: i370/velocity-filter-dynamic-offsets | Issue: #370

# Implementation Plan — Issue #370: Trade Velocity Filter & Dynamic Whole-Cent Quote Offsets

## Intake & Context Analysis
- **CodeRabbit Plan Intake**:
  - Adopted:
    - Phase 1: Real-time velocity & volatility range gate in `scripts/filter_markets.py` with stubbed tape test cases in `tests/test_velocity_gate.py`.
    - Phase 2: Dynamic whole-cent quote offset in `core_brain/quotes.py` and `core_brain/config.py` with pair-cost enforcement (`dynamic_pair_sum`) and test cases in `tests/test_quotes.py`.
    - Propagation of `range_cents` and `velocity_measured_at` through `core_brain/market_feed.py::GraduatedMarket` into `trader_loop._market_specs`.
  - Added based on Operator Directive:
    - Defined bundle of tournament variant presets (`aggressive`, `balanced`, `conservative`, `control`) in `core_brain/config.py` to allow running multiple parameter sets in parallel tournaments.
    - Whole-cent rounding without fractional half-cents (explicitly requested by operator for clean order placement on 0.01 tick grid).
  - Deferred to #371:
    - Multi-arm tournament supervisor process manager and multi-port UI dashboard switcher.

- **Resolved Open Questions (from Operator & Codebase)**:
  1. *Lookback window & velocity threshold:* Default 30-minute window, minimum 8 trades, last trade within 5 minutes (300s), and non-zero price range.
  2. *Rounding policy for dynamic offsets:* Round to whole integer cents (0.01 tick) clamped between 1¢ and 4¢ (`dynamic_offset_min_cents`, `dynamic_offset_max_cents`) to prevent fractional cent clutter.
  3. *Pair-cost safety gate:* The dynamic offset must strictly preserve `price_UP + price_DOWN < 1.00`. If dynamic offset pricing would cause combined pair cost >= 1.00, refuse both intents with reason `dynamic_pair_sum`.
  4. *Multi-variant bundle:* Built-in preset profiles (`aggressive`, `balanced`, `conservative`, `control`) ready for tournament arms.

## Dependency Graph
- Task 1: Trade Velocity & Volatility Gate in Market Screener
  └──> Task 2: Opt-in Dynamic Integer-Cent Quote Offset & Tournament Variant Presets
        └──> Task 3: Feed Integration & Regression Verification

---

## Tasks

### Task 1: [Backend/Logic] Real-Time Trade Velocity & Volatility Gate in Market Screener [x]
- **Size**: S
- **Domain Tag**: `[Backend/Logic]`
- **Helper Skill**: `test-driven-development`
- **Target Files**: `scripts/filter_markets.py`, `core_brain/market_feed.py`, `tests/test_velocity_gate.py`
- **Depends on**: None
- **Description**:
  1. Add `trade_velocity_and_range()` in `scripts/filter_markets.py` to evaluate trades within the lookback window:
     - Count total trades in window.
     - Verify last trade age <= max allowed age.
     - Measure high-low price range in cents: `(max_price - min_price) * 100.0`.
  2. Implement `velocity_reject(trade_count, min_trades, last_trade_sec, max_last_sec, range_cents) -> tuple[bool, str]`:
     - Reject if trade count < min_trades (e.g. < 8 trades).
     - Reject if last trade is too stale (e.g. > 300s).
     - Reject if price range is flat (0.0 cents).
  3. Add optional `range_cents` and `velocity_measured_at` to `GraduatedMarket` in `core_brain/market_feed.py`.
  4. Write `tests/test_velocity_gate.py` covering flat tape, active tape, stale tape, and missing tape handling.
- **Verification**: `python -m pytest -q tests/test_velocity_gate.py`

### Task 2: [Backend/Logic] Opt-in Dynamic Integer-Cent Quote Offset & Tournament Variant Presets [x]
- **Size**: M
- **Domain Tag**: `[Backend/Logic]`
- **Helper Skill**: `test-driven-development`
- **Target Files**: `core_brain/config.py`, `core_brain/quotes.py`, `tests/test_quotes.py`
- **Depends on**: Task 1
- **Description**:
  1. In `core_brain/config.py`, add dynamic offset parameters and multi-variant preset registry:
     - `dynamic_offset_enabled: bool = False`
     - `dynamic_offset_multiplier: float = 0.5`
     - `dynamic_offset_min_cents: int = 1`
     - `dynamic_offset_max_cents: int = 4`
     - `dynamic_offset_max_age_sec: float = 1800.0`
     - `measured_range_cents: Optional[float] = None`
     - `measured_range_at: Optional[float] = None`
     - Preset bundle: `TOURNAMENT_PRESETS` with `aggressive` (mult 0.25), `balanced` (mult 0.50), `conservative` (mult 0.75), and `control` (static baseline).
     - Bounded environment variable overrides: `HUNTER_DYNAMIC_OFFSET`, `HUNTER_DYNAMIC_OFFSET_MULT`, etc.
  2. In `core_brain/quotes.py`:
     - Compute dynamic base offset: `clamp(round(multiplier * range_cents), min_cents, max_cents) / 100.0`.
     - Round to whole integer cents without fractional half-cents.
     - Enforce `price_UP + price_DOWN < 1.00`. If dynamic offset results in `>= 1.00`, refuse intents with reason `dynamic_pair_sum`.
  3. Write `tests/test_quotes.py` testing integer-cent offsets, clamping, preset application, fallback on stale data, and pair-cost re-gate safety.
- **Verification**: `python -m pytest -q tests/test_quotes.py`

### Task 3: [Integration/Regression] Screener-to-Trader Feed Integration & Safety Verification
- **Size**: S
- **Domain Tag**: `[Backend/Logic]`
- **Helper Skill**: `test-driven-development`
- **Target Files**: `core_brain/trader_loop.py`, `tests/test_market_feed.py`, `tests/test_trader_loop.py`
- **Depends on**: Task 2
- **Description**:
  1. Ensure `core_brain/trader_loop.py` copies `range_cents` and `velocity_measured_at` from `spec` into `MakerConfig`.
  2. Verify that when `HUNTER_DYNAMIC_OFFSET=0` (default), quoting produces identical output to static baseline.
  3. Verify regression test suites pass cleanly across touched modules.
- **Verification**: `python -m pytest -q tests/test_velocity_gate.py tests/test_quotes.py tests/test_market_feed.py tests/test_trader_loop.py`

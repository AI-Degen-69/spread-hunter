# Checklist — Issue #370

- [x] **Task 1: [Backend/Logic] Real-Time Trade Velocity & Volatility Gate in Market Screener**
  - [x] Add `trade_velocity_and_range` and `velocity_reject` in `scripts/filter_markets.py`
  - [x] Add optional `range_cents` and `velocity_measured_at` in `core_brain/market_feed.py`
  - [x] Implement unit tests in `tests/test_velocity_gate.py`
  - [x] Verify: `python -m pytest -q tests/test_velocity_gate.py`

- [x] **Task 2: [Backend/Logic] Opt-in Dynamic Integer-Cent Quote Offset & Pair-Cost Bounds**
  - [x] Add dynamic offset configuration & env overrides in `core_brain/config.py`
  - [x] Implement whole-cent dynamic offset calculation and `dynamic_pair_sum` refusal in `core_brain/quotes.py`
  - [x] Implement unit tests in `tests/test_dynamic_offset.py`
  - [x] Verify: `python -m pytest -q tests/test_dynamic_offset.py`

- [x] **Task 3: [Integration/Regression] Screener-to-Trader Feed Integration & Safety Verification**
  - [x] Wire range fields into `core_brain/trader_loop.py`
  - [x] Verify backward compatibility and baseline equivalence when disabled
  - [x] Run full targeted regression suite: `python -m pytest -q tests/test_velocity_gate.py tests/test_dynamic_offset.py tests/test_market_feed.py tests/test_trader_loop.py`

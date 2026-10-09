# Implementation Plan — #434: Implement Price Volatility Gate with Live Sports and Gaming Priority

Branch: i434/implement-price-volatility-gate-with-live-sports | Issue: #434

## Intake & CodeRabbit Synthesis
- **Adopted from CodeRabbit:**
  - Introduce configurable 2-hour volatility window (`select_volatility_window_sec = 7200.0`, env `HUNTER_VOLATILITY_WINDOW_SEC`). Keep volume movement window at 30 minutes (`1800.0` seconds).
  - Enforce price volatility / range bar: minimum 2.0 cents ($0.02) swing (`high - low >= $0.02`).
  - Clear, distinct rejection reason: `flat market: price swing {r_str} in last {window_str} < {min_range_cents:.2f}c`.
  - Sports & eSports detection helper `is_sports_or_esports` in `scoring/selector.py` matching `sports_market_type` or sports series keywords across title, event_title, series_title, slug, category.
  - Exemption: active live sports/eSports markets (`_live_event` or `live_event: true` AND `is_sports_or_esports`) are exempt from the flat range rejection (`range_exempt=True` in `velocity_gate_reject`).
  - Flag `volatility_exempt: True` on the market row.
  - Sorting priority: `sort_eligible` prioritizes exempt live sports markets ahead of non-exempt markets, preserving primary return ranking (`-rank_score`) within each group.
- **Adjustments / Consolidations:**
  - Consolidated into 3 vertical slices adhering to TDD.
  - Rejection text formatted dynamically for window (e.g. `2h` for 7200s, `30m` for 1800s).
  - `tape_movement_and_range` accepts `range_window_sec: float | None = None` so price swing can evaluate over 2 hours while `movement_usd` evaluates over 30 minutes.

## Goal & Acceptance Criteria
- Markets with price swing < 2.0c over 2h are rejected with reason `flat market: price swing < 2.00c in last 2h` (unless live sports/eSports).
- Live sports and eSports markets are exempted from the swing requirement and tagged `volatility_exempt: True`.
- `sort_eligible` places `volatility_exempt: True` markets at the top of the eligible queue.
- Unmeasured tape remains fail-open.
- Focused test suite in `tests/test_velocity_gate.py` passes 100%.

---

## Task Breakdown

### Task 1: Volatility Window & Range Gate Configuration with Sports/eSports Detector [Core/Logic] [Size: S] [x]
- **Target files:** `scoring/config.py`, `scoring/selector.py`, `tests/test_velocity_gate.py`
- **Depends on:** None
- **What is built:**
  - In `scoring/config.py`:
    - Add `select_volatility_window_sec: float = 7200.0` to `MakerConfig`.
    - Update `select_min_range_cents: float = 2.0` default.
    - Add env override for `HUNTER_VOLATILITY_WINDOW_SEC` in `load()`.
  - In `scoring/selector.py`:
    - Add `is_sports_or_esports(title=None, slug=None, category=None, series_title=None, event_title=None, sports_market_type=None) -> bool`. Matches `sports_market_type` or `_SPORTS_SERIES_RE` or sports keywords.
  - In `tests/test_velocity_gate.py`:
    - Add unit tests for `is_sports_or_esports` against various tennis, League of Legends, CS2, NFL, and non-sports macro/crypto titles.
- **Verification:** `python -m pytest -q tests/test_velocity_gate.py -k "test_is_sports"`

### Task 2: Range Exemption & Live Priority Sorting in Screener [Core/Logic] [Size: M] [x]
- **Target files:** `scripts/filter_markets.py`, `tests/test_velocity_gate.py`
- **Depends on:** Task 1
- **What is built:**
  - In `scripts/filter_markets.py`:
    - Update module constants: `VOLATILITY_WINDOW_SEC = getattr(_CFG, "select_volatility_window_sec", 7200.0)`, `MIN_RANGE_CENTS = getattr(_CFG, "select_min_range_cents", 2.0)`.
    - Update `tape_movement_and_range`: add `range_window_sec: Optional[float] = None` (defaults to `VOLATILITY_WINDOW_SEC`). Calculate `prices_in_window` within `now - range_window_sec` while volume and trades use `window_sec` (30m).
    - Update `velocity_gate_reject`: add `range_exempt: bool = False`, `range_window_sec: Optional[float] = None`. When `range_exempt=True`, skip min range check. When rejected for range, output reason: `f"flat market: price swing {r_str} in last {win_str} < {min_range_cents:.2f}c"`.
    - In `evaluate`:
      - Detect live sports/esports via `is_sports_or_esports(...)` and `m.get("_live_event") or m.get("live_event")`.
      - Pass `range_exempt` to `velocity_gate_reject`.
      - Attach `"volatility_exempt": is_exempt` to market row.
    - In `sort_eligible`:
      - Sort by `(not r.get("volatility_exempt", False), -rank_score(r))`.
    - In `_cause`:
      - Map `"flat market"` to bucket `"flat market"`.
  - In `tests/test_velocity_gate.py`:
    - Add tests for 2-hour lookback range calculation with 30-minute volume.
    - Add tests for `velocity_gate_reject` with `range_exempt=True`.
    - Add tests for `sort_eligible` prioritizing exempt markets.
- **Verification:** `python -m pytest -q tests/test_velocity_gate.py`

### Task 3: Regression Suite, Gate Verification & Diagnostics [Core/Logic] [Size: S] [x]
- **Target files:** `tests/test_velocity_gate.py`, `tests/scoring/test_markets.py`, `tests/test_unified_universe.py`
- **Depends on:** Task 1, Task 2
- **What is built:**
  - Update any existing tests checking the older `flat range: price range ...` string or old 1.0c default.
  - Verify that `sort_eligible` maintains strict order stability and correctly promotes live sports.
  - Verify full focused suite passes cleanly.
- **Verification:** `python -m pytest -q tests/test_velocity_gate.py tests/scoring/test_markets.py tests/test_unified_universe.py`

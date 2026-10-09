# Implementation Plan — #432: Update Screener Price Band to [0.15, 0.85] and Tighten Max Spread to 2c

Branch: i432/update-screener-price-band-to-015-085-and-tighten | Issue: #432

## Intake & CodeRabbit Synthesis
- **Adopted from CodeRabbit:**
  - Update mid-price band to `[0.15, 0.85]` across quoting (`core_brain/quotes.py`) and screening (`scripts/filter_markets.py`) with refusal strings matching `outside [0.15, 0.85]`.
  - Update `select_max_book_spread` to `0.0205` in `core_brain/config.py`, `scoring/config.py`, `scoring/selector.py` (`book_allowed`, `pair_books_allowed`), and `scripts/filter_markets.py`.
  - Floating point residue guard: round computed spread to 6 decimal places before `spread > max_spread` in `scoring/selector.py:book_allowed` to prevent IEEE-754 false rejections (e.g. `0.5105 - 0.49`).
  - Formatting `spread <= {spread_bar:.4f}` in `scripts/filter_markets.py` console print.
  - Updating dashboard telemetry fallback in `dashboard/static/app.js` and explainer copy in `dashboard/static/strategy_explainer.html`.
  - Specific test adjustments: moving test mids in `tests/test_live_event_discovery.py` to `0.88/0.12`, updating `tests/test_trader_loop.py`, `tests/test_unified_universe.py` fixtures (`0.49/0.51`), and `tests/test_wide_book_trial.py`.
- **Rejected from CodeRabbit:**
  - Over-splitting into separate sub-phases and speculative test fixtures with mock frameworks. Consolidated into 4 clear vertical slices.
- **Unverified items:** None. All line numbers, code symbols, and test expectations verified against live codebase.

## Goal & Acceptance Criteria
- Markets with mid prices in `[0.15, 0.85]` (e.g. `0.17` or `0.83`) pass the price band gate.
- Markets with order book spreads exceeding `0.0205` (e.g. `0.03`) are refused by the spread gate with `spread {spread:.4f} > {max_spread:.4f}`.
- Refusal messages consistently state `outside [0.15, 0.85]` for decided market/mid rejections.
- Zero regressions across targeted test suites.

## Improvement Proposal (Evidence-based)
- **Evidence:** `scoring/selector.py:204` calculates `spread = best_ask - best_bid` and checks `spread > max_spread`. With `max_spread = 0.0205`, `0.5105 - 0.49` evaluates to `0.020500000000000018`, failing the gate strictly due to IEEE-754 precision.
- **Classification:** Simplification / edge-case hardening (adopted by default).
- **Resolution:** In `scoring/selector.py:book_allowed`, round calculated spread to 6 decimal places (`round(best_ask - best_bid, 6)`) before comparing against `max_spread`.

---

## Task Breakdown

### Task 1: Update Mid-Price Band Gate to `[0.15, 0.85]` and Align Core Quoting/Filtering [x]
- **Size:** M
- **Domain Tag:** `[Backend/Logic]`
- **Helper Skill:** `test-driven-development`
- **Depends on:** None
- **Target Files:**
  - `core_brain/quotes.py`
  - `scripts/filter_markets.py`
  - `tests/test_live_quotes.py`
  - `tests/test_trader_loop.py`
  - `tests/test_live_event_discovery.py`
  - `tests/test_unified_universe.py`
- **Details:**
  - In `core_brain/quotes.py` (lines 350-351, 649-651), change `(mid <= 0.20 or mid >= 0.80)` to `(mid <= 0.15 or mid >= 0.85)` and refusal message to `f"{side}: mid {mid:.3f} outside [0.15,0.85] -- decided market"`.
  - In `scripts/filter_markets.py` (lines 1655, 1667), change `0.20 < mid < 0.80` to `0.15 < mid < 0.85` and rejection reason to `f"{side}: decided mid {mid:.2f} outside [0.15, 0.85]"`.
  - Update comments in `filter_markets.py` referencing the mid gate band.
  - Update test assertions in `tests/test_live_quotes.py`, `tests/test_trader_loop.py`, `tests/test_unified_universe.py`, and `tests/test_live_event_discovery.py` (moving test mid to `0.88/0.12`).
  - Add test cases proving that mid `0.17` and `0.83` are admitted while `0.14` and `0.86` are refused.
- **Verification:**
  - `python -m pytest -q tests/test_live_quotes.py tests/test_trader_loop.py tests/test_live_event_discovery.py tests/test_unified_universe.py`

### Task 2: Tighten Maximum Book Spread Gate to `0.0205` in Configs and Selector [x]
- **Size:** M
- **Domain Tag:** `[Backend/Logic]`
- **Helper Skill:** `test-driven-development`
- **Depends on:** Task 1
- **Target Files:**
  - `core_brain/config.py`
  - `scoring/config.py`
  - `scoring/selector.py`
  - `scripts/filter_markets.py`
- **Details:**
  - In `core_brain/config.py` line 544 and `scoring/config.py` line 519, set `select_max_book_spread: float = 0.0205`. Update comment to explain 2.05 cents in price units.
  - In `scoring/selector.py` (lines 180, 216), set default `max_spread: float = 0.0205` in `book_allowed` and `pair_books_allowed`.
  - In `scoring/selector.py:book_allowed`, round `spread = round(best_ask - best_bid, 6)`.
  - In `scripts/filter_markets.py` line 3368, update print format to `spread <= {spread_bar:.4f}`.
- **Verification:**
  - Unit tests asserting `select_max_book_spread == 0.0205` and boundary spread gating (`0.0205` passes, `0.0206` fails).

### Task 3: Update Dashboard Telemetry Copy & Explainer HTML [x]
- **Size:** S
- **Domain Tag:** `[Design/UI]`
- **Helper Skill:** `frontend-ui-engineering`
- **Depends on:** Task 2
- **Target Files:**
  - `dashboard/static/app.js`
  - `dashboard/static/strategy_explainer.html`
- **Details:**
  - In `dashboard/static/app.js`: update line 5768 `spreadGate` fallback from `0.06` to `0.0205`; update line 5785 mid value from `'Binary · Mid [0.20, 0.80]'` to `'Binary · Mid [0.15, 0.85]'`.
  - In `dashboard/static/strategy_explainer.html`: update line 385 from `spread < 6¢` to `spread ≤ 2.05¢ (0.0205)`; update line 576 diagram text from `Spread<6c` to `Spread≤2.05¢`.
- **Verification:**
  - Inspection of string rendering in `dashboard/static/app.js` and `dashboard/static/strategy_explainer.html`.

### Task 4: Align Regression Test Suites for Selector, Universe, Snapshot & Trial Bars
- **Size:** M
- **Domain Tag:** `[Backend/Logic]`
- **Helper Skill:** `test-driven-development`
- **Depends on:** Task 2, Task 3
- **Target Files:**
  - `tests/test_market_selection_bars.py`
  - `tests/test_wide_book_trial.py`
  - `tests/test_unified_universe.py`
  - `tests/test_pipeline_snapshot_gates.py`
- **Details:**
  - In `tests/test_market_selection_bars.py`: add tests for default `select_max_book_spread == 0.0205` and boundary checks for `book_allowed` with `0.49/0.5105` (passes) vs `0.4794/0.5000` (fails).
  - In `tests/test_wide_book_trial.py`: update `cfg.select_max_book_spread == 0.0205` in `test_load_leaves_the_ceilings_alone_when_unset`.
  - In `tests/test_unified_universe.py`: update mock book fixtures in `_FakeSession` from `0.48/0.52` to `0.49/0.51` (spread 0.02), update candidate `_spread` to `0.02`, pass explicit `max_spread=0.06` to `test_a_zero_ours_score_is_retained_as_a_rejection_row` (`_ZeroScoreSession` has spread 0.05).
  - Add tests in `tests/test_unified_universe.py` asserting `fm.MAX_BOOK_SPREAD == 0.0205` and rejection of spread `0.0300 > 0.0205`.
- **Verification:**
  - `python -m pytest -q tests/test_live_quotes.py tests/test_trader_loop.py tests/test_unified_universe.py tests/test_wide_book_trial.py tests/test_live_event_discovery.py tests/test_market_selection_bars.py tests/test_pipeline_snapshot_gates.py`

---

## Checkpoints
- **Checkpoint 1 (after Task 1):** Price band gate tests passing with new `[0.15, 0.85]` boundaries in quoting and filter_markets.
- **Checkpoint 2 (after Task 2 & 3):** Max book spread threshold `0.0205` and dashboard telemetry copy updated and consistent.
- **Checkpoint 3 (after Task 4):** All 7 targeted regression suites passing cleanly.

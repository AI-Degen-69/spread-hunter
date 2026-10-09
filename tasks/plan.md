# Implementation Plan — End-to-End Unit Tests for Redesigned Filter Pipeline
Branch: i435/test-end-to-end-unit-tests-for-redesigned-filter | Issue: #435

## Summary
Add comprehensive end-to-end unit tests covering the redesigned market filtering pipeline to verify that all screening gates execute in the proper order (fail-fast, cheap to expensive) and produce exact gate classifications. Tests validate the wide [0.15, 0.85] price band, tightened spread gate, live sports priority, and zero-reward independence without reaching the live venue.

## Intake & Seam Reconciliation (CodeRabbit Plan Intake)
- **Adopted from CodeRabbit plan:** 4-task vertical decomposition; offline fake session (`_FakeSession`, `_Resp`) recording request URLs to prove fail-fast order; temporary SQLite/feed files to test downstream order manager independence.
- **Resolved / Rejected:** CodeRabbit's assumption that the codebase still enforced strict [0.20, 0.80] was rejected; verified in `scripts/filter_markets.py:1684-1708` that PR #432 already widened the band to `[0.15, 0.85]`. CodeRabbit's `[UNVERIFIED]` volume bucket and reason were resolved: bucket is `"volume"` and reason is `f"24h volume ${volume_24h:,.0f} < ${effective_bar:,.0f}"`.
- **Known Code Gap Acknowledged:** In `scripts/filter_markets.py:1811-1847`, `evaluate()` does not propagate `_start_iso` or `gameStartTime` into the returned row, preventing `rank_score` from applying the 1.5x live boost without re-attaching the start timestamp. However, `volatility_exempt` is preserved, guaranteeing primary sort priority in `sort_eligible()`.

## Specification & Interfaces
- **Target File:** `tests/test_redesigned_filter_pipeline.py` (new standalone test suite).
- **Functions Under Test:**
  - `scripts.filter_markets.evaluate`
  - `scripts.filter_markets.score_pool`
  - `scripts.filter_markets._write_pipeline_snapshot`
  - `scripts.filter_markets._write_universe_file`
  - `scripts.filter_markets._cause`
  - `scripts.filter_markets.sort_eligible`
  - `scripts.filter_markets.rank_score`
  - `core_brain.order_manager.decide`
- **Protected Files (MUST NOT MODIFY):**
  - `scripts/filter_markets.py`
  - `scoring/selector.py`
  - `scoring/config.py`
  - `core_brain/order_manager.py`
  - `core_brain/order_registry.py`
  - `core_brain/markets.py`
  - `core_brain/market_feed.py`
  - `core_brain/quotes.py`
  - `data/orders.db`
  - `tests/conftest.py`

## Task Decomposition

### Task 1: Test Harness Setup & Price/Spread Edge Tests [x]
- **Task ID:** TASK-1
- **Size:** S
- **Domain Tag:** `[Core/Logic]`
- **Helper Skill:** `test-driven-development`
- **Depends on:** None
- **Files:** `tests/test_redesigned_filter_pipeline.py`
- **Description:**
  - Create `tests/test_redesigned_filter_pipeline.py` with docstring outlining gate order and threshold contract.
  - Implement offline test harness: `_FakeSession`, `_Resp` with request URL logging, `_candidate` builder with overrides, `_books` builder, and `_active_tape` / `_flat_tape` helpers.
  - Implement baseline test: active tape and deep books ($2,400) around 0.50 mid produce `eligible=True`.
  - Implement price edge tests: parametrized mid prices 0.10 and 0.90 fail with `YES: decided mid {mid:.2f} outside [0.15, 0.85]` and bucket `YES decided mid`; combined case (out-of-band mid with wide spread and thin depth) still fails with price reason; boundary mid 0.15 is rejected.
  - Implement spread edge tests: parametrized spread with `max_spread=0.02`: spread 0.03 fails with `YES: spread 0.0300 > 0.0200` and bucket `YES spread`; spread 0.01 succeeds; wide spread with thin depth fails with spread reason (spread check precedes depth check).
- **Verification:** `python -m pytest -q tests/test_redesigned_filter_pipeline.py -k "test_baseline or test_price or test_spread"`

### Task 2: Gate Evaluation Order Matrix & Snapshot Accounting Tests [x]
- **Task ID:** TASK-2
- **Size:** M
- **Domain Tag:** `[Core/Logic]`
- **Helper Skill:** `test-driven-development`
- **Depends on:** TASK-1
- **Files:** `tests/test_redesigned_filter_pipeline.py`
- **Description:**
  - Implement parametrized gate evaluation order test verifying fail-fast progression and request URL log:
    - Row 1: Identity refusal (0 HTTP requests made).
    - Row 2: In-play clock refusal (0 HTTP requests made).
    - Row 3: Expired end date refusal (0 HTTP requests made).
    - Row 4: Flat tape movement refusal (1 tape HTTP request, 0 book HTTP requests).
    - Row 5: Velocity gate refusal (1 tape HTTP request, 0 book HTTP requests).
    - Row 6: Decided mid refusal (tape request followed by 2 book requests).
    - Row 7: Thin book depth refusal (tape request followed by 2 book requests).
    - Row 8: Low 24h volume refusal (tape request followed by 2 book requests).
    - Row 9: Distant horizon refusal (tape request followed by 2 book requests).
  - Verify tape request always precedes book requests when network is reached.
  - Implement snapshot & universe accounting test:
    - Score pool of candidates via `score_pool(..., max_workers=1)`.
    - Generate `pipeline.json` via `_write_pipeline_snapshot(...)` and `market_universe.json` via `_write_universe_file(...)` in `tmp_path`.
    - Assert `scored == rejected + eligible`, `attempted == scored + dropped_no_verdict`, sum of bucket `n` equals `rejected`.
    - Verify rejection format (`cause`, `n`, `would_fund`, `traps`, `examples`), example caps (<=4 per bucket), and presence of eligible rows in `final`.
- **Verification:** `python -m pytest -q tests/test_redesigned_filter_pipeline.py -k "test_gate_order or test_pipeline_snapshot"`

### Checkpoint 1 (After Tasks 1 & 2)
Verify basic screening gates, edge cases, fail-fast order, and snapshot persistence cleanly pass without hitting the venue.

### Task 3: Live Sports & eSports Market Priority Tests
- **Task ID:** TASK-3
- **Size:** S
- **Domain Tag:** `[Core/Logic]`
- **Helper Skill:** `test-driven-development`
- **Depends on:** TASK-1
- **Files:** `tests/test_redesigned_filter_pipeline.py`
- **Description:**
  - Build live CS2/LoL/esports candidate with `_live_event=True`, past kickoff, volume between live bar ($10k) and normal bar ($125k) (e.g. $48,000), active tape, deep in-band books.
  - Verify `merge_live_event_markets` merges the live copy over the scanned copy.
  - Verify `evaluate` produces `eligible=True` and `volatility_exempt=True`, bypassing clock and volatility gates while still clearing live volume ($10,000) and book spread gates.
  - Verify that if spread is wide, even a live market is rejected with the spread reason (non-bypass of safety gates).
  - Verify ranking behavior: `sort_eligible` places `volatility_exempt` live market at the top ahead of non-exempt markets; verify `rank_score` live boost (1.5x) when start time is provided.
- **Verification:** `python -m pytest -q tests/test_redesigned_filter_pipeline.py -k "test_live"`

### Task 4: Zero-Reward Market Independence & Downstream Decide Verification
- **Task ID:** TASK-4
- **Size:** S
- **Domain Tag:** `[Core/Logic]`
- **Helper Skill:** `test-driven-development`
- **Depends on:** TASK-1
- **Files:** `tests/test_redesigned_filter_pipeline.py`
- **Description:**
  - Screening test: evaluate candidate with `source="spread"` and empty/default `rewards` block. Verify `eligible=True`, `daily=0.0`, positive spread income.
  - Downstream decide test: write mock `markets.json` with a zero-reward spread market (`source="spread"`, `daily=0.0`), point `DEFAULT_MARKETS_PATH` via monkeypatch.
  - Mock network calls for `fetch_pinned_market`, `full_book`, and `fetch_live_balance` (returning None).
  - Execute `decide(target="0", db_path=tmp_path / "live.db")`.
  - Verify using `OrderRegistry(db_path=tmp_path / "live.db")` that 2 intents are generated, market events recorded with `QUOTING` and `INTENT_GENERATED`, hedge census recorded, and 0 live orders placed.
  - Run regression suites: `tests/test_pipeline_snapshot_gates.py`, `tests/test_unified_universe.py`, `tests/test_velocity_gate.py`.
- **Verification:** `python -m pytest -q tests/test_redesigned_filter_pipeline.py tests/test_pipeline_snapshot_gates.py tests/test_unified_universe.py tests/test_velocity_gate.py`

### Task 5: Visual UI Verification & Pipeline Kanban Preview
- **Task ID:** TASK-5
- **Size:** S
- **Domain Tag:** `[Design/UI]`
- **Helper Skill:** `frontend-ui-engineering`
- **Depends on:** TASK-2
- **Files:** `scripts/preview_filter_pipeline.py` (or demonstration fixture in `runtime/pipeline.json`)
- **Description:**
  - Provide a standalone runner script `scripts/preview_filter_pipeline.py` (or test export) that populates `runtime/pipeline.json` and `runtime/market_universe.json` with the representative redesigned pipeline candidates (price edges, spread rejections, live sports priority, eligible winners).
  - Allow the operator to launch `python -m dashboard.server` and navigate to `http://127.0.0.1:8799` -> Tab 3 ("Market Filter Pipeline").
  - Visually verify on the Kanban board:
    1. Rejection cards in their respective columns (Price band `[0.15, 0.85]`, Spread > 0.02, Depth, Volume).
    2. Prioritized Live Sports & eSports badges and cards in the Passed/Eligible column.
    3. Pipeline telemetry header (gates, snapshot timestamp, candidate counts).
- **Verification (Hands-on for Operator):**
  - Run `python -m scripts.preview_filter_pipeline`
  - Open `http://127.0.0.1:8799` and switch to Tab 3 (Market Filter Pipeline) to see the visual Kanban board.

### Checkpoint 2 (After Tasks 3, 4 & 5)
Complete suite validation: all test groups pass, regression suites pass, and the visual Kanban pipeline board renders correctly in the dashboard UI.


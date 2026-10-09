Branch: i431/remove-maker-rewards-requirement-from-screener | Issue: #431

# Implementation Plan — Remove Maker Rewards Requirement from Screener Filters (#431)

## Summary & Goal
Allow markets with $0/day maker rewards to pass the screener and pinned market fetching routines as long as spread and resolution horizon criteria are satisfied. Pure spread-capture pair trades do not rely on maker reward subsidies.

## CodeRabbit Plan Intake
- **Adopted**: Removal of `require_rewards` from `fetch_pinned_market` in both `scoring/markets.py` and `core_brain/markets.py`; removal of `MIN_PAYOUT` and `FLOOR_MULTIPLE` from `scripts/filter_markets.py`; standardization of funnel snapshot `reward_min_income_usd_day` to `0.0`; dashboard stage-7 label update to Horizon & Income.
- **Rejected**: Any modifications to `scoring/selector.py` (which already has no reward check) or `tradable()` / `MAX_DAYS_TO_RESOLVE` (must remain strictly locked).
- **Unverified**: None. All references and call-sites verified directly in codebase.

## Improvement Proposal (Adopted Simplification)
- **Evidence**: `scripts/filter_markets.py` exported `reward_min_income_usd_day` as `1.5` and `spread_min_income_usd_day` as `0.0`, resulting in dual-bar UI text on the dashboard.
- **Proposal**: Unify both thresholds to `0.0` in the pipeline snapshot and consolidate the dashboard Stage 7 hero into a single clean rule: `≤ 30.0 days · income > $0.00/day`.

---

## Tasks

### Task 1: Core & Scoring Pinned Fetch Refactor [Backend/Logic] [Size: S] [x]
- **Depends on**: None
- **Files**:
  - `scoring/markets.py`
  - `core_brain/markets.py`
  - `core_brain/order_manager.py`
  - `core_brain/trader_loop.py`
  - `core_brain/markout.py`
  - `core_brain/audit.py`
- **Description**:
  - Remove `require_rewards` parameter and the `if require_rewards and daily <= 0:` refusal in `scoring/markets.py:fetch_pinned_market` and `core_brain/markets.py:fetch_pinned_market`.
  - Preserve `daily` sum calculation, closed-market checks, accepting-orders checks, token checks, and `game_start_time` handling.
  - Remove `require_rewards=False` argument from call sites in `core_brain/order_manager.py`, `core_brain/trader_loop.py`, `core_brain/markout.py`, and `core_brain/audit.py`.
  - Update comments in `core_brain/order_manager.py:449-455`.
- **Verification**: `python -m pytest -q tests/scoring/test_markets.py tests/test_order_manager.py`

### Task 2: Screener Filter & Funnel Snapshot Standardization [Backend/Logic] [Size: S] [x]
- **Depends on**: Task 1
- **Files**:
  - `scripts/filter_markets.py`
- **Description**:
  - Remove `MIN_PAYOUT` and `FLOOR_MULTIPLE` constants.
  - In `evaluate()`, check `income > 0` for both `rewards` and `spread` sources. Use reason `"no reward income"` for rewards and `"no spread income"` for spread.
  - Confirm `_cause` correctly categorizes `"no reward income"` and `"no spread income"` to `income`.
  - In snapshot export, set `"reward_min_income_usd_day": 0.0`.
  - Update `--legacy-rewards` CLI help and output text to reflect `income > $0/day` across both sources without payout floor.
- **Verification**: `python -m pytest -q tests/test_pipeline_snapshot_gates.py tests/test_unified_universe.py tests/test_live_funnel.py`

### Task 3: Dashboard Stage-7 Hero & Label Alignment [Design/UI] [Size: XS] [x]
- **Depends on**: Task 2
- **Files**:
  - `dashboard/static/app.js`
- **Description**:
  - Rename `7. Horizon & Yield Gate` in `BUCKET_DEFS` / `STAGE_DEFS` to `7. Horizon & Income Gate` (keep `key: 'horizon'`).
  - Update `getStageHero('horizon', funnel)`: set `param` to `TEST: HORIZON & INCOME` and `value` to `≤ ${Number(horizonDays).toFixed(1)} days · income > $0.00/day`.
  - Update `reward_min_income_usd_day` fallback from `1.5` to `0`.
  - Ensure `categorizeGate` preserves grouping for both `income` and legacy `payout` causes.
- **Verification**: `python -m pytest -q tests/test_dashboard_server.py`

### Task 4: Comprehensive Test Suite & Regression Verification [Backend/Logic] [Size: S] [x]
- **Depends on**: Task 1, Task 2, Task 3
- **Files**:
  - `tests/scoring/test_markets.py`
  - `tests/test_order_manager.py`
  - `tests/test_pre_start_gate.py`
  - `tests/test_single_buy_saver.py`
  - `tests/test_market_quote.py`
  - `tests/test_markout_maturity.py`
  - `tests/test_milestone7_telemetry.py`
  - `tests/test_shadow_markouts.py`
  - `tests/test_pipeline_snapshot_gates.py`
  - `tests/test_unified_universe.py`
  - `tests/test_dashboard_server.py`
- **Description**:
  - Add real pinned-fetch tests for zero-reward markets and closed markets in `tests/scoring/test_markets.py` and `tests/test_order_manager.py`.
  - Update `test_quote_does_not_require_maker_rewards` and fakes in `tests/test_single_buy_saver.py`, `tests/test_pre_start_gate.py`, etc.
  - Update snapshot assertions in `tests/test_pipeline_snapshot_gates.py` to expect `0.0`.
  - Add/update tests in `tests/test_unified_universe.py` confirming zero-reward spread markets pass horizon gate while out-of-horizon markets are rejected.
  - Update dashboard server tests for the new stage-7 title and fallback.
- **Verification**:
  `python -m pytest -q tests/test_pipeline_snapshot_gates.py tests/test_order_manager.py tests/scoring/test_markets.py tests/test_unified_universe.py tests/test_single_buy_saver.py tests/test_pre_start_gate.py tests/test_dashboard_server.py tests/test_live_funnel.py tests/test_market_quote.py tests/test_markout_maturity.py tests/test_milestone7_telemetry.py tests/test_shadow_markouts.py`

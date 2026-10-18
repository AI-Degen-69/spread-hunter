# Implementation Plan - Issue #681: Purge surviving maker-rewards formulas and vocabulary from filter_markets.py and scoring

Branch: refactor/purge-rewards-filter-markets-681 | Issue: #681
Execution Mode: Step-by-step
Task Type: Code (Refactor / Cleanup)
Size Tier: Standard (3 files touched, straightforward removal of dead code and legacy calculations)

## Summary
Purge legacy maker rebate/liquidity reward program formulas, calculations, and terminal vocabulary from `scripts/filter_markets.py` and delete orphaned `scoring/rewards.py`. The screener will rank purely on spread-capture movement notional without simulating reward pool splits.

## Intake Notes & Seams
- **CodeRabbit Plan:** Not posted at intake time; all seams verified directly against the live repository.
- **Seams verified verbatim:**
  - `scoring/rewards.py` exists (113 lines) with zero active imports anywhere in the repository.
  - `scripts/filter_markets.py:1211` defines `order_score(v, s, size, min_size)`, called only at line 1671.
  - `scripts/filter_markets.py:148` defines `q_min(a, b)`, called only for `their_score` and `ours`.
  - `scripts/filter_markets.py:1456` checks `len(toks) != 2 or OFFSET >= v`, where `v` is reward max spread.
  - `scripts/filter_markets.py:1612-1620` calculates competitor quadratic order score `q1` and `q2`.
  - `scripts/filter_markets.py:1678` calculates `income = rate * ours / (ours + theirs)`.
  - `scripts/filter_markets.py:1749-1752` populates `est_income`, `est_capital`, `return_pct_day`, `their_score`.
  - `scripts/filter_markets.py:3169` prints `income > $0/day`.
  - `scripts/filter_markets.py:3213-3224` prints `picked X spread / Y reward`, table with `$/day` and `ret%/d`, and `TOTAL capital ... income .../day`.
- **Committed Enhancement (Adopt-by-default):** In `evaluate()`, eliminate the `OFFSET >= v` rejection on `v = rw.get('max_spread')`. Require only `len(toks) == 2` for binary markets. This ensures legitimate binary spread markets are not rejected simply because the venue configured a tight maker-rewards window.

---

## Tasks

### T1: Delete orphaned scoring/rewards.py and lock zero-reference guard [Backend/Logic] [Size: XS] [x]
- **Depends on:** None
- **Files touched:**
  - `scoring/rewards.py` (delete)
  - `tests/test_rewards_purge.py` (add regression tests)
- **Description:**
  - Remove `scoring/rewards.py`.
  - Add test in `tests/test_rewards_purge.py` ensuring `scoring.rewards` cannot be imported and no dead references remain.
- **Verification:**
  - `python -m pytest -q tests/test_rewards_purge.py`

### T2: Purge reward pool formulas, checks, and income fields from filter_markets.py [Backend/Logic] [Size: M] [x]
- **Depends on:** T1
- **Files touched:**
  - `scripts/filter_markets.py`
  - `tests/test_unified_universe.py`
  - `tests/test_redesigned_filter_pipeline.py`
  - `tests/test_rewards_purge.py`
- **Description:**
  - In `scripts/filter_markets.py`:
    - Remove `order_score` and `q_min` functions.
    - In `evaluate()`: Remove `rw.get("max_spread")` check and `OFFSET >= v` rejection; require strictly `len(toks) == 2`.
    - In `evaluate()`: Remove competitor quadratic book scoring loop (`q1`, `q2`, `theirs`) and `ours = q_min(sides[0], sides[1])` rejection.
    - In `evaluate()`: Remove pool-split `income = rate * ours / (ours + theirs)` and reward income gate `pays = income > 0 ...`. A market is eligible if `can_trade` and `movement_usd is not None and movement_usd > 0`.
    - Cleanly default or retain compatibility for row fields (`est_income: 0.0`, `est_capital: 0.0`, `return_pct_day: 0.0`, `their_score: 0.0`) so downstream readers (e.g. `core_brain/market_feed.py`, `core_brain/kpi.py`) do not encounter missing keys.
    - Update `rank_score(row)` to use `movement_usd` as base score (falling back to `return_pct_day` if provided in unit test fixtures).
    - Update `build_paired_admission_bundle` to sort on `rank_score` rather than `return_pct_day`.
  - Update tests:
    - Update `test_a_zero_ours_score_is_retained_as_a_rejection_row` in `tests/test_unified_universe.py` to assert that pure spread markets are no longer rejected on reward pool share.
    - Update `tests/test_redesigned_filter_pipeline.py:819-820` to verify clean eligibility.
    - Add tests in `tests/test_rewards_purge.py` asserting no reward pool math is performed.
- **Verification:**
  - `python -m pytest -q tests/test_rewards_purge.py tests/test_unified_universe.py tests/test_redesigned_filter_pipeline.py tests/test_rank_score.py tests/test_paired_depth.py`

### T3: Clean up screener terminal output and summary banners [UX/Copy] [Size: S]
- **Depends on:** T2
- **Files touched:**
  - `scripts/filter_markets.py`
  - `tests/test_rewards_purge.py`
  - `tests/test_filter_markets_out_dir.py`
- **Description:**
  - In `scripts/filter_markets.py:main()`:
    - Remove `income > $0/day` from `gates` banner string.
    - Replace `picked X spread / Y reward` with `picked N spread markets`.
    - Replace the reward table header (`$/day`, `ret%/d`, `capital`) and row format with clean spread capture metrics: `market`, `spread`, `volume_24h`, `movement_30m`, `depth`.
    - Remove `TOTAL capital ... income .../day` footer summary.
    - Remove `--legacy-rewards` argument or keep it raising an explicit clean error.
  - Update tests:
    - Add test in `tests/test_rewards_purge.py` capturing `main()` terminal output to verify zero reward terms appear.
    - Ensure `tests/test_filter_markets_out_dir.py` passes cleanly.
- **Verification:**
  - `python -m pytest -q tests/test_rewards_purge.py tests/test_filter_markets_out_dir.py`
  - Hands-on manual run: `python -m scripts.rank_markets --dry-run`

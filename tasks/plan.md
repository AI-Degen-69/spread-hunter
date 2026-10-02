# Plan: Issue #339 — Ladder rungs priced off live book

Branch: `i339/ladder-rungs-are-priced-off-a-constant-050` | Issue: `#339`
Stack: Python 3.11+, pytest
Size Tier: Standard (3-4 files)
Task Type: Code, Debug

## Context & CodeRabbit Plan Intake
- **Adopted**: Deriving rung prices directly from `best_bid` (`best_bid - (i - 1) * tick`) with `completable_pair_block` gating; reporting distinct quoted prices in `build_report` when `rungs` is omitted.
- **Rejected**: Complex multi-band mid-point offset heuristics or changes to production `MakerConfig` defaults (unnecessary complexity; standard `best_bid - (i - 1) * tick` matches the maker touch perfectly).
- **Unverified**: Tape prints on live venue outside the trial window.

## Improvement Proposal (Adopted by default)
- **Proposal**: In `scripts/ladder_shadow_rehearsal.py` and `scripts/ladder_live_books_trial.py`, when `rungs` is empty or omitted in `build_report`, populate `rungs` from the unique order prices (`sorted({o["price"] for o in orders if o.get("price") is not None})`) so trial reports truthfully reflect what was quoted.
- **Evidence**: Verbatim quote from Issue #339 review comment: *"CodeRabbit read the committed trial report and noticed its rungs field is empty... The value has to come from the store's own orders rows, so it stays truthful once the rungs are book-derived."*

---

## Tasks Breakdown

### Task 1: [Backend/Logic] Derive rung prices from live book in `decide_ladder_quotes`
- **ID**: `T1`
- **Size**: S
- **Target Files**: `core_brain/quotes.py`
- **Description**: Update `decide_ladder_quotes` in `core_brain/quotes.py` to calculate each rung price as `round(best_bid - (i - 1) * tick, 4)` for `i` in `1..cfg.ladder_rungs`. Guard prices with `0 < price < 1.0` and `risk.completable_pair_block(cfg, price, hedge_ask)`. If no rungs complete under the cap, return `[], "ladder: no rung completes under the cap"`. Update docstring to reflect live book derivation.
- **Helper Skill**: `test-driven-development`
- **Depends on**: None
- **Verification**: `python -m pytest -q tests/test_ladder_quotes.py`

### Task 2: [Backend/Logic] Populate report `rungs` from store orders when omitted
- **ID**: `T2`
- **Size**: S
- **Target Files**: `scripts/ladder_shadow_rehearsal.py`, `scripts/ladder_live_books_trial.py`
- **Description**: In `scripts/ladder_shadow_rehearsal.py:build_report`, if `rungs` is empty or falsy, set `report["rungs"]` to `sorted({o["price"] for o in orders if o.get("price") is not None})`.
- **Helper Skill**: `incremental-implementation`
- **Depends on**: `T1`
- **Verification**: `python -m pytest -q tests/test_ladder_shadow_rehearsal.py tests/test_ladder_live_books_trial.py`

### Task 3: [Backend/Logic] Comprehensive test suite for book-derived ladder quotes
- **ID**: `T3`
- **Size**: S
- **Target Files**: `tests/test_ladder_quotes.py`, `tests/test_ladder_live_books_trial.py`
- **Description**: Add unit tests in `tests/test_ladder_quotes.py` covering:
  1. Asymmetric volatile books (e.g. UP bid 0.68 / DOWN bid 0.28) quoting at touch (0.68, 0.67 and 0.28, 0.27).
  2. Books exceeding completable cap (e.g. UP bid 0.60 + DOWN ask 0.45 >= 0.99) posting nothing.
  3. Single-tick gaps and zero-budget guards.
  4. Test that `build_report` reflects actual quoted rungs from store orders when `rungs=()`.
- **Helper Skill**: `test-driven-development`
- **Depends on**: `T1`, `T2`
- **Verification**: `python -m pytest -q tests/test_ladder_quotes.py tests/test_ladder_live_books_trial.py tests/test_ladder_shadow_rehearsal.py`

---

## Checkpoints
- **Checkpoint 1 (after T1)**: `decide_ladder_quotes` prices from `best_bid` and passes focused tests.
- **Checkpoint 2 (after T2 & T3)**: Full ladder test suite green with truthful report generation.

# Plan 001 — Fix time-bomb trial-market test fixture

**Category:** Correctness / test reliability (CI-blocking)
**Confidence:** HIGH  **Effort:** S
**Git commit stamped:** `948ce5a59ec64f80548c27c040aa0c3089ac388e`

## Problem

`tests/test_selection.py::test_evaluate_gates_on_the_trial_depth_bar_when_passed`
now FAILS on the live calendar (2026-08-20).

Root cause: the fixture `_trial_market()` (line 420) hardcodes:
```python
"end_date_iso": "2026-08-20T00:00:00Z",   # tests/test_selection.py:435
```
`rank_markets.evaluate` → `tradable()` → `days_to_resolve(end_iso)` compares the
venue end date to `datetime.now(timezone.utc)`. Now that the calendar has reached
or passed 2026-08-20, `days_to_resolve` returns ≤ 0, so `tradable` returns
`(False, "horizon passed")` and the market is rejected before the depth gate ever
runs. The test's "admitted under trial bar" assertion therefore fails.

The sibling `_volume_trial_market()` (line 515) hardcodes `"2026-08-25T00:00:00Z"`
(line 532) — 5 days out today, so it still passes, but it is the same time-bomb
and will fail after 2026-08-25.

## Why it is a real bug, not a flaky test

The selection logic is correct (horizon gate is intended). The defect is the
*test fixture* baking an absolute calendar date. Any test that depends on "today
is before <fixed date>" silently rots. The fix is to derive the dates relative to
"now" inside the fixture so the trial shape (near-future horizon, ~10–15 days)
survives indefinitely.

## Current state (excerpt)

`tests/test_selection.py`:
```python
def _trial_market():                       # line 420
    return {
        ...
        "end_date_iso": "2026-08-20T00:00:00Z",   # line 435  <-- time bomb
        "_volume_24h": 1_000_000.0,
        "_spread": 0.02,
    }

def _volume_trial_market():                # line 515
    return {
        ...
        "end_date_iso": "2026-08-25T00:00:00Z",   # line 532  <-- time bomb (later)
        ...
    }
```

`rank_markets.tradable` horizon gate (scripts/rank_markets.py:142-147):
```python
if days < 0:
    return False, "horizon passed"
if days > MAX_DAYS_TO_RESOLVE:             # 30.0 (strategy/config.py:391)
    return False, f"horizon {days:.1f}d > {MAX_DAYS_TO_RESOLVE:.0f}d"
```

## Fix

Make both fixtures compute a near-future ISO at call time. Add an import of
`timedelta`/`timezone` if not already present (the file already imports
`datetime` at the top via `from datetime import ...` — check; if only `datetime`
is imported, add `timedelta, timezone`).

1. In `_trial_market()`, replace the literal with:
   ```python
   "end_date_iso": (datetime.now(timezone.utc) + timedelta(days=12)).isoformat(),
   ```
   (12 days keeps it well under `MAX_DAYS_TO_RESOLVE=30` and positive.)

2. In `_volume_trial_market()`, replace the literal with:
   ```python
   "end_date_iso": (datetime.now(timezone.utc) + timedelta(days=15)).isoformat(),
   ```

3. Confirm the file's import line covers `timedelta` and `timezone`. If the top
   import is `from datetime import datetime` only, change/add to
   `from datetime import datetime, timedelta, timezone`.

## Verification gates

```
cd "<repo root>"
uv run pytest -q tests/test_selection.py
```
Expected: the previously-failing `test_evaluate_gates_on_the_trial_depth_bar_when_passed`
passes, and `test_evaluate_gates_on_the_volume_trial_bar_when_passed` (the test
that uses `_volume_trial_market`) still passes. Full file: 0 failures.

Positive check (not just "no error"):
```
uv run pytest -q "tests/test_selection.py::test_evaluate_gates_on_the_trial_depth_bar_when_passed" -v 2>&1 | grep -E "PASSED|FAILED"
```
Expected: `PASSED`.

## STOP conditions

- If `days_to_resolve` or `tradable` has been refactored since the stamp such that
  the horizon gate no longer uses `end_date_iso`, STOP and report — the fixture
  change alone won't fix a logic change; re-derive the intended trial shape.
- If the import edit would collide with an existing `timedelta`/`timezone` import,
  reuse the existing one; do not duplicate imports.
- Do NOT change `MAX_DAYS_TO_RESOLVE` or the gate logic — the production behavior
  is correct. Only the fixture dates change.

## Out of scope

No change to `scripts/rank_markets.py`, no change to strategy config, no change to
the other 700+ passing sim tests.

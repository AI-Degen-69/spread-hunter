Branch: i357/expired-markets-stay-in-scan-universe | Issue: #357

# Implementation Plan — Prune Expired Markets at Intake (#357)

## Problem Summary
Markets whose resolution date has already passed (e.g. 10-16 hours ago) were not refused by `tradable()` when `end_date_iso` was present, because of an assumption that any open market past `endDate` was an in-play sports kickoff. As a result, expired non-sports and completed matches were marked eligible, published to `markets.json`, and repeatedly scanned every cycle only to be rejected at quote time (`t_remaining < 0`), burning cycles and API calls.

## CodeRabbit Intake Note
- **Adopted**: Pure helper `expired_at_intake` near `days_to_resolve` and early gate in `evaluate` before queue/tape/books; mapping into `horizon` bucket.
- **Hardened**: CodeRabbit flagged potential policy conflict with `_live_sports_market` having no `gameStartTime`. Hardened `expired_at_intake` to also inspect `category == "Sports"` so live sports markets past kickoff continue to reach books as tested in #312.
- **Unverified items**: None.

## Tasks

### Task 1: Expiry Helper & Early Admission Gate [Backend/Logic] [S] [x]
- Target: `scripts/filter_markets.py`
- Description:
  - Add pure helper `expired_at_intake(end_iso, start_iso=None, category=None, now_iso=None) -> tuple[bool, str]`.
    - Returns `(False, "")` if `days_to_resolve(end_iso, now_iso=now_iso)` is `None` (unreadable/missing) or `>= 0` (open window).
    - Returns `(False, "")` if `start_iso is not None` or `str(category or "").strip().lower() == "sports"` (sports kickoff exception).
    - If `days < 0`: returns `(True, f"horizon passed (expired {when} ago)")` where `when` is formatted hours/days.
  - In `evaluate()`, directly after `pre_start` (around line 927):
    - Call `expired_at_intake(m.get("end_date_iso"), market_start_iso(m), category=m.get("category") or m.get("venue_category"))`.
    - If expired, return `_reject_row(source, expired_reason, m, volume_24h)` immediately before tape and book fetches.
- Depends on: None
- Verification: `python -m pytest -q tests/test_unified_universe.py`

### Task 2: Focused Unit & End-to-End Admission Tests [Backend/Logic] [S] [x]
- Target: `tests/test_unified_universe.py`
- Description:
  - Test `expired_at_intake` helper directly: past end non-sports returns `(True, reason)`, future end returns `(False, "")`, missing end returns `(False, "")`, sports market past end returns `(False, "")`.
  - Test `evaluate` early refusal: verify an expired non-sports candidate is rejected with `horizon passed` before any tape or book fetch (using `_BoomOnBooksSession` / raising session).
  - Verify existing tests (`test_an_eligible_live_market_past_kickoff_reaches_the_books`, `test_tradable_admits_a_live_market_past_kickoff_to_the_horizon_arm`) pass without regression.
- Depends on: Task 1
- Verification: `python -m pytest -q tests/test_unified_universe.py`

### Task 3: Verification & Clean Rehearsal Check [Backend/Logic] [XS] [x]
- Target: `tests/test_unified_universe.py`, `tests/test_cycle_stream.py`
- Description:
  - Run focused test suites `tests/test_unified_universe.py tests/test_cycle_stream.py`.
  - Verify that no live store was touched.
- Depends on: Task 2
- Verification: `python -m pytest -q tests/test_unified_universe.py tests/test_cycle_stream.py`

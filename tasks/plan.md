Branch: i356/cycle-intent-submitted-reads-0 | Issue: #356

# Implementation Plan — Issue #356: Repair cycle_intent.submitted accounting & telemetry gap

## Intake & CodeRabbit Analysis Disposition
- **Adopted from CodeRabbit Plan**: Cursor rowcount warning on 0-row UPDATE in `_update_cycle_intent`, missing DB path warning, `PARTIAL_SUBMIT_PLACED_ATTR` exception attachment in `trader_loop` and `shadow_exec`, and targeted tests in `test_cycle_stream.py` and `test_run_attribution.py`.
- **Rejected from CodeRabbit Plan**: None. Merged its multi-phase suggestions into 3 atomic tasks to maintain simplicity and avoid unnecessary abstractions.
- **[UNVERIFIED] items**: Exact historical reason for the Oct 3 discrepancy (scratch DB `01_shadow_12-09_00-58.db` is not in git repo); resolved by documenting that `cycle_intent` is a 200-row rolling window of visit intents, not daily cumulative posted orders.

## Evidence-Based Improvement Proposal
- **Proposal**: In `core_brain/cycle_stream.py::_update_cycle_intent`, capture the cursor from `conn.execute(...)` and check `cur.rowcount == 0`. When 0 rows were updated, emit a stderr warning: `WARNING: cycle_intent update matched 0 rows: market_slug={market_slug} cycle={cycle} run_id={run_id}`. Also emit a warning if `not p.exists()`.
- **Evidence**: Currently `_update_cycle_intent` (lines 238-255) silently returns if `not p.exists()`, and silently executes an update whose subquery evaluates to `NULL` if no matching row exists, hiding key/attribution bugs.
- **Classification**: Simplification / edge-case hardening (adopted by default).

## Tasks

### Task 1: [Backend/Logic] Telemetry hardening & Unmatched Update Warning [x]
- **Size**: S
- **Domain Tag**: `[Backend/Logic]`
- **Helper Skill**: `test-driven-development`
- **Target Files**:
  - `core_brain/cycle_stream.py`
  - `tests/test_cycle_stream.py`
- **Depends on**: None
- **Description**:
  1. In `core_brain/cycle_stream.py::_update_cycle_intent`, before returning on missing `db_path`, write `WARNING: cycle_intent update skipped, db missing: {p}` to `sys.stderr`.
  2. Capture `cur = conn.execute(...)`. If `cur.rowcount == 0`, write `WARNING: cycle_intent update matched 0 rows: market_slug={market_slug} cycle={cycle} run_id={run_id}` to `sys.stderr`.
  3. Add test `test_cycle_intent_unmatched_update_warns` in `tests/test_cycle_stream.py` using `capsys` to assert stderr warning and confirm no row updated.
- **Verification Method**: `python -m pytest -q tests/test_cycle_stream.py`

### Task 2: [Backend/Logic] Partial-submit accounting across quote loop & shadow executor [x]
- **Size**: M
- **Domain Tag**: `[Backend/Logic]`
- **Helper Skill**: `test-driven-development`
- **Target Files**:
  - `core_brain/trader_loop.py`
  - `core_brain/shadow_exec.py`
  - `tests/test_run_attribution.py`
- **Depends on**: Task 1
- **Description**:
  1. Define constant `PARTIAL_SUBMIT_PLACED_ATTR = "_partial_submitted_count"` in `core_brain/trader_loop.py`.
  2. In `core_brain/trader_loop.py::_submit_intents`, attach `setattr(exc, PARTIAL_SUBMIT_PLACED_ATTR, placed)` if an exception occurs after legs are committed/posted or on partial post rollback.
  3. In `core_brain/trader_loop.py::_visit_one`, in `except Exception as e:`, extract `submitted = getattr(e, PARTIAL_SUBMIT_PLACED_ATTR, submitted)` so that `market_error` emission and `LiveFleetResult` carry the actual placed count.
  4. In `core_brain/shadow_exec.py::record_submit`, import `PARTIAL_SUBMIT_PLACED_ATTR` and attach `setattr(exc, PARTIAL_SUBMIT_PLACED_ATTR, placed)` in the `except Exception as exc:` handler before `raise`.
  5. In `tests/test_run_attribution.py`, add integration test asserting that real shadow submit (`run_shadow`) updates `submitted` in `cycle_intent`. Add test asserting partial submission failure sets `submitted` on `market_error` and `cycle_intent`.
- **Verification Method**: `python -m pytest -q tests/test_run_attribution.py tests/test_trader_loop.py`

### Task 3: [Docs] Document `cycle_intent.submitted` semantics and discrepancy context [x]
- **Size**: S
- **Domain Tag**: `[Docs]`
- **Helper Skill**: `documentation-and-adrs`
- **Target Files**:
  - `core_brain/cycle_stream.py`
  - `docs/issues/351-noticed-but-not-touching.md`
  - `docs/issues/analysis-01-shadow-zero-fill.md`
- **Depends on**: Task 2
- **Description**:
  1. In `core_brain/cycle_stream.py`, update table comment at lines 62-66 and docstrings of `_write_cycle_intent` and `_update_cycle_intent` clarifying that `submitted` is a per-visit quote loop counter for the retained 200 rows, not cumulative daily orders.
  2. Update candidate N2 in `docs/issues/351-noticed-but-not-touching.md` and add clarifying note in `docs/issues/analysis-01-shadow-zero-fill.md`.
- **Verification Method**: `python -m pytest -q tests/test_cycle_stream.py tests/test_run_attribution.py`

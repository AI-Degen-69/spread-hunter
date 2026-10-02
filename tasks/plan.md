# Plan: Issue #337 — ENGINE pill provenance, stall reason, and other-store live run notice

Branch: `i337/engine-pill-provenance-and-stall-reason` | Issue: #337

## Task Breakdown

- [x] **Task 1 [Backend/Logic] [M]**: Backend telemetry & seam additions
  - **Files**: `core_brain/trader_loop.py`, `scripts/ladder_live_books_trial.py`, `dashboard/server.py`
  - **Description**:
    - Add `markets_fn_empty_is_routine: bool = False` to `VenueSeam` in `core_brain/trader_loop.py`. If set, log empty market refreshes as INFO instead of WARNING.
    - Set `markets_fn_empty_is_routine=True` in `scripts/ladder_live_books_trial.py`.
    - In `dashboard/server.py`:
      - Extend `_read_shadow_heartbeat_file` to include `heartbeat_file`, `db_path`, `pid_alive`, and `end_reason` (`finished`, `process_gone`, `heartbeat_stale`, or `None`). Use `process_started_at` falling back to `started_at` in `_is_pid_alive()`.
      - Update `read_shadow_run()` to evaluate all candidates and prefer running runs over stale ones on the same store.
      - Add `read_other_live_shadow_runs(active_db_path, now)` returning identity-only list (`run_id`, `db_path`, `heartbeat_file`) of live runs on different stores.
      - Add keyword-only `ended: bool = False` to `compute_scan_state()`.
      - Extend `get_scan_state()` to return `heartbeat_source` (`kind`, `file`, `run_id`, `db_path`, `pid`), `stall_reason` (`finished`, `process_gone`, `heartbeat_stale`, `no_heartbeat`, `heartbeat_unreadable`), and `other_live_runs`.
  - **Depends on**: None
  - **Verification**: `python -m pytest -q tests/test_per_run_shadow_heartbeat.py tests/test_scan_state_shadow.py tests/test_dashboard_server.py`

- [x] **Task 2 [Design/UI] [M]**: Frontend ENGINE pill provenance tooltip, stall reasons, and other-store live run notice
  - **Files**: `dashboard/static/index.html`, `dashboard/static/styles.css`, `dashboard/static/app.js`
  - **Description**:
    - Add `#scan-engine-elsewhere` neutral tag next to `#scan-state-pill` in `index.html`.
    - Add styling rules in `styles.css` for `#scan-engine-elsewhere` using existing dashed neutral scope-tag styling.
    - In `app.js`:
      - Add `engineReasonText(reason)` and `engineProvenanceText(payload)` pure helpers.
      - Update `renderScanStatePill()` to construct detailed tooltip with source kind, exact file, run ID, store, and verdict reason.
      - Update `scanPillState(state, age, stallReason)` to map `STALLED` + `finished` -> `STOPPED` and `STALLED` + other -> `DOWN`.
      - Render `#scan-engine-elsewhere` when `other_live_runs` has entries and ENGINE is not RUNNING, displaying count and tooltip with other runs' identities and disclaimer "its numbers are not shown on this page".
  - **Depends on**: Task 1
  - **Verification**: Node test harness `tests/js/live_state_harness.cjs` & `tests/test_live_state_language.py`

- [x] **Task 3 [Code/Tests] [M]**: Regression test coverage & verification
  - **Files**: `tests/test_per_run_shadow_heartbeat.py`, `tests/test_scan_state_shadow.py`, `tests/test_dashboard_server.py`, `tests/test_live_state_language.py`, `tests/js/live_state_harness.cjs`, `tests/test_trader_loop.py`
  - **Description**:
    - Add unit tests for `markets_fn_empty_is_routine` in `test_trader_loop.py`.
    - Add unit tests in `test_per_run_shadow_heartbeat.py` for `heartbeat_file`, `end_reason`, candidate preference, and `read_other_live_shadow_runs`.
    - Add unit tests in `test_scan_state_shadow.py` for each stall reason and heartbeat source scenario.
    - Update `live_state_harness.cjs` and `test_live_state_language.py` to verify JS rendering for ENGINE pill states and other-store notices.
  - **Depends on**: Task 1, Task 2
  - **Verification**: `python -m pytest -q tests/test_per_run_shadow_heartbeat.py tests/test_scan_state_shadow.py tests/test_live_state_language.py tests/test_dashboard_server.py tests/test_trader_loop.py`

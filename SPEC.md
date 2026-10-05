# SPEC: Issue #371 — Multi-arm tournament UI: port isolation, indexed scratch stores, and dashboard run switcher

## Goal
Provide multi-arm tournament visualization and execution orchestration where each test arm runs independently with its own designated port, isolated scratch database, unique indexing scheme, and seamless switching directly in the dashboard UI.

## Acceptance Criteria
- [ ] Tournament runner launches independent arms with distinct scratch databases named by issue, arm index, and arm name (`data/<issue>_tournament_<idx>_<arm>_<stamp>.db`) and run IDs (`shadow-<issue>-t<idx>-<arm>-<stamp>`).
- [ ] Each trial arm executes in isolation without lock collisions, port conflicts, or shared state interference.
- [ ] Dashboard discovers tournament runs and displays unique arm index, port/pid, and status (`RUNNING` / `FINISHED`) in the run switcher UI.
- [ ] Dedicated dashboard port links (`:<port>`) rendered in the run switcher when separate dashboard instances are running.
- [ ] Switching active database via `/api/system/db` successfully switches active telemetry without server restart and invalidates cached snapshots from the previous database.
- [ ] Finished/ended shadow runs retain their per-run ring telemetry when switched to, instead of falling back to unrelated live event rings.
- [ ] The dashboard server accurately reports its bound port in status telemetry (`services.dash.port`).
- [ ] Comprehensive unit and integration test suites pass cleanly: `python -m pytest -q tests/test_live_dash.py tests/test_shadow_tournament.py tests/test_live_state_language.py`.

## Scope
### In scope
- Tournament DB and run ID naming and parsing functions.
- Adding `--dash-port` metadata argument to `core_brain/shadow_run.py`.
- Enhancing `dashboard/server.py` with tournament metadata extraction, index-based sorting, per-run ring retention for finished runs, and accurate port reporting.
- Frontend run switcher improvements in `dashboard/static/app.js`: `runSwitcherLabel`, dedicated port links, and cached snapshot invalidation on store switch.
- Adding `scripts/shadow_tournament.py` launcher supporting dry-run planning, port probing, isolated env pass-through, and graceful child process cleanup.
- Test coverage across `tests/test_live_dash.py`, `tests/test_shadow_tournament.py`, and `tests/test_live_state_language.py`.

### Out of scope
- Modifying production `data/orders.db` (strictly scratch database stores only).
- Placing real venue orders (shadow / paper execution only).
- Overriding core order management safety checks or touching live port 8799 for testing.

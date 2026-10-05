Branch: i371/multi-arm-tournament-ui-port-isolation | Issue: #371

# Implementation Plan — Issue #371: Multi-arm tournament UI: port isolation, indexed scratch stores, and dashboard run switcher

## Intake & Context Analysis
- **CodeRabbit Plan Intake**:
  - Adopted:
    - Structured DB naming and regex parser: `data/<issue>_tournament_<idx>_<arm>_<stamp>.db` and run ID `shadow-<issue>-t<idx>-<arm>-<stamp>`.
    - Optional `--dash-port` flag in `core_brain/shadow_run.py` stamped into heartbeat JSON.
    - Dashboard `list_shadow_runs` parser for `tournament` metadata and `dash_port`.
    - Preserved sorting: running first, then newest, and ordered by arm index within the same issue/stamp tournament group.
    - Preserved per-run ring resolution for FINISHED/ENDED shadow runs.
    - Port handoff to guarantee status `services.dash.port` reports the actual bound port.
    - Frontend `runSwitcherLabel(run)` helper and dedicated port link `:<port>`.
    - Clearing retained poll snapshots on store switch.
    - Python launcher `scripts/shadow_tournament.py` with pure plan builder `build_tournament_plan` and process isolation.
  - Added / Enhanced based on Evidence & Operator Directives:
    - Default tournament presets fallback: When `--arms-file` is omitted, `--preset-arms` automatically sets up the built-in profiles from `core_brain.config.TOURNAMENT_PRESETS` (`aggressive`, `balanced`, `conservative`, `control`).
    - Base port default: 8801 to avoid colliding with live 8799 or test runners.

- **Resolved Open Questions**:
  1. *Port reservation:* Default live port 8799 is reserved for live execution; tournament dashboards allocate starting from 8801+ (or user-specified `--base-port`).
  2. *Single vs multi-page view:* The UI supports BOTH! You can switch the active DB on a single dashboard page (via `/api/system/db`), OR click the dedicated port link (`:<port>`) to open separate tabs side-by-side.
  3. *Snapshot invalidation on switch:* When switching DBs, clear all retained last-known-good poll snapshots (`lastState`, `lastKpi`, `lastStatus`, `lastScanState`, `lastTrialReadiness`, `lastGuardHealth`, `lastGuardAlerts`, `lastStatusForRuns`, etc.) so old run KPIs do not leak into the new view.

## Dependency Graph
- Task 1: Naming Convention, Metadata Parser, & Rehearsal Dash-Port Flag
  └──> Task 2: Dashboard Server Tournament Run Listing, Port Reporting & Ring Resolution
        ├──> Task 3: Run Switcher Labeling, Dedicated Port Links, and Snapshot Invalidation
        └──> Task 4: Multi-Arm Tournament Launcher Orchestrator

---

## Tasks

### Task 1: [Backend/Logic] Naming Convention, Metadata Parser, & Rehearsal Dash-Port Flag [x]
- **Size**: S
- **Domain Tag**: `[Backend/Logic]`
- **Helper Skill**: `test-driven-development`
- **Target Files**: `core_brain/shadow_run.py`, `dashboard/server.py`, `tests/test_shadow_tournament.py`
- **Depends on**: None
- **Description**:
  1. Define naming and parsing helpers in `core_brain/shadow_run.py` (and export/use in `dashboard/server.py`):
     - `parse_tournament_db_path(path: str | Path) -> dict | None`: Parses `^(?:.*[\\/])?(?P<issue>\d+)_tournament_(?P<index>\d+)_(?P<arm>[a-z0-9-]+)_(?P<stamp>\d{8}-\d{6})\.db$` returning `{"issue": int, "index": int, "arm": str, "stamp": str}`. Does not match `NN_shadow_*.db`.
     - `build_tournament_db_path(issue: int | str, index: int, arm: str, stamp: str, base_dir: Path | str = "data") -> Path`.
     - `build_tournament_run_id(issue: int | str, index: int, arm: str, stamp: str) -> str`: Produces `shadow-<issue>-t<idx>-<arm>-<stamp>` (< 64 chars, regex valid).
  2. In `core_brain/shadow_run.py`:
     - Add `--dash-port` argument (1 <= port <= 65535) to `_parse_args`.
     - Pass `dash_port` to `run_shadow` and `write_shadow_heartbeat`.
     - When `dash_port` is provided, include `"dash_port": int(dash_port)` in the heartbeat payload.
  3. Create `tests/test_shadow_tournament.py` with unit tests for naming helpers, regex validation, run ID length limits, and heartbeat `dash_port` writing.
- **Verification**: `python -m pytest -q tests/test_shadow_tournament.py`

### Task 2: [Backend/Logic] Dashboard Server Tournament Run Listing, Port Reporting & Ring Resolution [ ]
- **Size**: M
- **Domain Tag**: `[Backend/Logic]`
- **Helper Skill**: `test-driven-development`
- **Target Files**: `dashboard/server.py`, `tests/test_live_dash.py`
- **Depends on**: Task 1
- **Description**:
  1. In `dashboard/server.py`:
     - In `_read_shadow_heartbeat_file(...)`, extract `"dash_port": raw.get("dash_port")` and `"tournament": parse_tournament_db_path(heartbeat_db)`.
     - In `list_shadow_runs(...)`: sort running first, then freshness, and within the same tournament issue/stamp group, order by arm index ascending.
     - In `_resolve_shadow_ring_path()`: keep `_ACTIVE_RING_OVERRIDE` first. When `_recent_shadow_run(active_db)` returns a matching run with a run ID, return the per-run ring file if it exists, regardless of whether the run is RUNNING, FINISHED, or ENDED (so completed runs retain their event rings).
     - In `main()`: set `os.environ["PORT"] = str(port)`, initialize `_ACTIVE_PORT` from `PORT` env var on module load, and pass `app` directly to `uvicorn.run(app, ...)` when `not args.reload` so `services.dash.port` in `/api/system/status` always reports the bound port.
  2. Create `tests/test_live_dash.py` with tests using `TestClient` and `tmp_path`:
     - Tournament heartbeat metadata discovery and index ordering.
     - Authorized database switching via `/api/system/db?db=...` updating status.
     - Retention of per-run ring for finished shadow runs.
     - Accurate port reporting in `services.dash.port`.
- **Verification**: `python -m pytest -q tests/test_live_dash.py tests/test_dashboard_run_switcher.py tests/test_scan_state_shadow.py`

### Task 3: [Design/UI] Run Switcher Labeling, Dedicated Port Links, and Snapshot Invalidation [ ]
- **Size**: S
- **Domain Tag**: `[Design/UI]`
- **Helper Skill**: `frontend-ui-engineering`
- **Target Files**: `dashboard/static/app.js`, `tests/js/live_state_harness.cjs`, `tests/test_live_state_language.py`
- **Depends on**: Task 2
- **Description**:
  1. In `dashboard/static/app.js`:
     - Implement `runSwitcherLabel(run)`:
       - For tournament runs (`run.tournament` present): `#<idx> <arm>` (e.g. `#01 aggressive`), with optional `:<dash_port>` and `(pid <pid>)`.
       - For standard runs: base file name of `db_path`.
     - In `renderRunSwitcher()`:
       - Use `runSwitcherLabel(r)` for the row title.
       - If `r.dash_port` is present, render a dedicated dashboard link (`[🔗 :<port>]` target `_blank`).
       - On successful database switch: clear retained snapshot variables (`lastState`, `lastKpi`, `lastStatus`, `lastScanState`, `lastTrialReadiness`, `lastGuardHealth`, `lastGuardAlerts`, `lastStatusForRuns`, `lastScanStateAtMs`, `lastKpiAtMs`, `lastStatusAtMs`) to prevent stale KPIs from leaking into the switched store view before polling fresh data.
     - Export `runSwitcherLabel` in `module.exports`.
  2. In `tests/js/live_state_harness.cjs` and `tests/test_live_state_language.py`:
     - Add `runswitcher` test harness mode to verify label formatting for tournament and non-tournament runs.
- **Verification**: `python -m pytest -q tests/test_live_state_language.py`

### Task 4: [Backend/Logic] Multi-Arm Tournament Launcher Orchestrator [ ]
- **Size**: M
- **Domain Tag**: `[Backend/Logic]`
- **Helper Skill**: `test-driven-development`
- **Target Files**: `scripts/shadow_tournament.py`, `tests/test_shadow_tournament.py`
- **Depends on**: Task 1, Task 2
- **Description**:
  1. Create `scripts/shadow_tournament.py`:
     - Implement pure plan builder `build_tournament_plan(...)`:
       - Accepts issue, arms specification (or defaults to `TOURNAMENT_PRESETS`), base_port (default 8801), minutes, interval, etc.
       - Validates: arm names are `[a-z0-9-]{1,24}`, no duplicates, only `HUNTER_*` environment variable overrides.
       - Validates target DB paths do not exist and are not `data/orders.db`.
       - Checks planned dashboard ports by binding a temporary socket on `127.0.0.1`.
       - Generates unique DB path, run ID, and argv for each arm.
     - Implement `launch_tournament(...)`:
       - Spawns shadow run processes with isolated environments.
       - If `--dashboards` is set, spawns dashboard server processes on designated ports.
       - Writes summary record to `runtime/tournaments/<issue>_<stamp>.json`.
       - Traps SIGINT / SIGTERM to cleanly terminate all spawned child processes.
     - Add `--dry-run` flag to output the execution plan JSON without starting processes.
  2. In `tests/test_shadow_tournament.py`:
     - Test plan generator with valid and invalid configurations (duplicate names, non-`HUNTER_` env, occupied ports, `orders.db` rejection).
     - Test dry-run execution.
- **Verification**: `python -m pytest -q tests/test_shadow_tournament.py tests/test_live_dash.py`

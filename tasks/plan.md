# Plan — #485 [ADD] Tournament Hub and Cockpit one-click launcher buttons in main dashboard

Branch: i485/tournament-hub-cockpit-launcher | Issue: #485
Size: Standard | Type: Code (Backend API + UI rail links + launcher handler)
Stack: Python (FastAPI, pytest), Vanilla HTML/JS (`prototype.js`, `reporting_launcher.js`, `index.html`, `styles.css`)
Labels: enhancement, ready-for-agent

## CodeRabbit plan intake

Adopted from the issue thread with simplicity enforcement:
- Backend: `GET /api/services/{name}/status` and `POST /api/services/{name}/start` for `name in ("hub", "cockpit")`.
- Ports: Hub runs on port 8798 (`scripts.tournament_hub`), Cockpit runs on port 8800 (`scripts.tournament_cockpit_server`).
- Probe checks port readiness via socket probe, state classification (`stopped`, `starting`, `running`, `conflict`, `failed`).
- Detached background execution via `subprocess.Popen` (OS-specific detach, `CREATE_NEW_PROCESS_GROUP` on Windows, `start_new_session` on POSIX).
- Frontend: Add Tournament Hub (:8798) to `RAIL_LINKS` in `dashboard/static/prototype.js` alongside existing Cockpit (:8800).
- Strict separation: Zero `fetch(` or `/api/` in `prototype.js` (keeps existing architectural invariant tested by `test_sidebar_prototype.py`).
- Dedicated client helper `dashboard/static/reporting_launcher.js` loaded in `index.html` to handle status polling, start trigger, and tab opening.

## One improvement proposal (Step 5)

**Adopted (simplification):** Instead of an overly complex multi-tier heartbeat parser for fingerprints, verify service port and basic HTTP response header / endpoint probe (`/api/tournament` for hub, `/api/state` for cockpit). If port is occupied by an alien service that does not respond, report `conflict`. This is resilient, fast, and stdlib-only.

## Locked constraints (CONSTRAINTS.md)

- Focused suites that must pass:
  - `tests/test_dashboard_server.py`
  - `tests/test_sidebar_prototype.py`
  - `tests/test_ts_frontend_contract.py`
- Protected files: `core_brain/order_manager.py`, `core_brain/order_registry.py`, `data/orders.db`.
- No new external packages.
- Security: `POST /api/services/{name}/start` guarded by `_authorize_control(request)`.

## Dependency graph

- Task 1 (Backend endpoints in `dashboard/server.py`) — unblocks Task 3.
- Task 2 (Rail navigation links and status dot in `prototype.js` and `prototype.css`) — unblocks Task 3.
- Task 3 (Client launcher script `reporting_launcher.js` + `index.html` + `test_ts_frontend_contract.py`) — Depends on: Task 1, Task 2.
- Task 4 (End-to-end verification and regression gate) — Depends on: Task 3.

## Tasks

### Task 1 — Backend service status and start endpoints (Size: M)
- Domain: Backend/Logic. Files: `dashboard/server.py`, `tests/test_dashboard_server.py`.
- Add `GET /api/services/{name}/status` and `POST /api/services/{name}/start` for `hub` and `cockpit`.
- Implement safe subprocess spawning with OS-specific detachment, socket port probing, and state detection.
- Add unit tests covering status (stopped, running, conflict, 404 for unknown service) and start (spawns once, 403 unauthorized, 409 conflict).
- Helper skill: `test-driven-development`.
- Verify: `python -m pytest -q tests/test_dashboard_server.py -k "services"`

### Task 2 — Rail links & launcher attributes in prototype.js (Size: S)
- Domain: Design/UI. Files: `dashboard/static/prototype.js`, `dashboard/static/prototype.css`, `tests/test_sidebar_prototype.py`.
- Add Tournament Hub (`http://127.0.0.1:8798`) to `RAIL_LINKS` with launcher key `hub`.
- Add launcher key `cockpit` to Tournament Cockpit.
- In `buildRailLinks`: attach `data-launcher="{key}"` and status indicator dot `<span class="proto-service-dot dot-stopped" aria-hidden="true"></span>`.
- In `prototype.css`: add status dot styles for running (green), starting (amber pulse), stopped (neutral/dim), failed (red), conflict (warning).
- In `initPrototype`: ensure click listener only switches pages for `[data-page]`, letting launcher/external links navigate cleanly.
- Verify: `python -m pytest -q tests/test_sidebar_prototype.py`

### Task 3 — Reporting launcher script & frontend wiring (Size: M)
- Domain: Frontend/Logic. Files: `dashboard/static/reporting_launcher.js`, `dashboard/static/index.html`, `tests/test_ts_frontend_contract.py`.
- Create `dashboard/static/reporting_launcher.js`:
  - Listens to clicks on `[data-launcher]`.
  - If service is stopped, triggers `POST /api/services/{name}/start`, polls `/api/services/{name}/status` until active, and navigates.
  - Polls `/api/services/{name}/status` periodically to update indicator dots and tooltips.
- Load `reporting_launcher.js` in `dashboard/static/index.html` right after `prototype.js`.
- Update `tests/test_ts_frontend_contract.py` to recognize the new reporting launcher endpoints.
- Verify: `python -m pytest -q tests/test_ts_frontend_contract.py tests/test_sidebar_prototype.py`

### Task 4 — Final verification & regression run (Size: S)
- Run all focused suites:
  `python -m pytest -q tests/test_dashboard_server.py tests/test_sidebar_prototype.py tests/test_ts_frontend_contract.py`
- Verify git status clean, prepare for Station IV review & PR.

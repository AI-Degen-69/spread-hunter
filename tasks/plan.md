# Execution Plan: Issue #225 — Move market scan status pill to top bar and format seconds over 60s as minutes

## Classification & Stack
- **Size Tier:** Standard (touches 4 files: HTML, JS, CSS, test suite)
- **Task Domain:** `[Design/UI]` + `[Frontend/Logic]`
- **Stack:** Python 3.11+, FastAPI / Vanilla JS, HTML5, CSS3, pytest

## Context & Problem
Currently, `#scan-state-pill` (which reports the trading loop's heartbeat from `runtime/shadow_run.json` or `runtime/live_poll_heartbeat.json`) is rendered inside `#screener-header` on the Market Filter tab (`#tab-3` / `data-markets`). When operators navigate other tabs (Home, Strategy, Trades, Reports), this status is hidden. Furthermore, when the scan stalls or runs long, raw seconds like `349s` or `1200s` clutter the pill instead of human-readable minutes (`1m`, `5m`).

## Proposed Out-of-the-Box Improvement
- **Dynamic Tooltip with Exact Age:** In addition to formatting `>= 60s` as clean minutes (e.g. `1m`, `5m`) inside the pill, dynamically update the pill's `title` attribute to show the exact elapsed seconds and source file (e.g. `Trading loop heartbeat: 349s ago (runtime/shadow_run.json)`). This provides high-level legibility at a glance without losing second-level diagnostic precision on hover.

---

## Tasks

### Task 1: [Design/UI] Relocate `#scan-state-pill` to Top Bar & Preserve Screener Header [x]
- **Target Files:** `dashboard/static/index.html`
- **Helper Skill:** `frontend-ui-engineering`, `modern-web-guidance`
- **Description:**
  - Move `<span id="scan-state-pill" class="pill stopped mono" ...>--</span>` into `<div class="top-meta">` alongside `#db-mode-badge` and `#usdc-balance`.
  - In `#screener-header`, keep the `TRADING LOOP` indicator and heartbeat context cleanly formatted without breaking layout.
- **Verification:** Inspection of HTML structure and existing tests in `tests/test_live_state_language.py`.

### Task 2: [Frontend/Logic] Format Heartbeat Duration as Minutes for `>= 60s` [x]
- **Target Files:** `dashboard/static/app.js`
- **Helper Skill:** `frontend-ui-engineering`, `test-driven-development`
- **Description:**
  - Introduce duration formatting helper `formatHeartbeatAge(seconds)`: `< 60s` -> `${s}s`, `>= 60s` -> `${Math.floor(s / 60)}m`.
  - Update `renderScreener` / `scan-state-pill` rendering to use this formatting.
  - Dynamically set `title` on `#scan-state-pill` with exact elapsed seconds and source.
  - Ensure `scan-state-pill` updates reliably during polling.
- **Verification:** Browser preview / unit assertions on `app.js` output and regex pattern checks.

### Task 3: [Design/UI] Responsive Top Nav Styling & Alignment Check [x]
- **Target Files:** `dashboard/static/styles.css`
- **Helper Skill:** `frontend-ui-engineering`
- **Description:**
  - Ensure `.top-meta` gap, wrap, and pill heights remain harmonious on desktop (1440px/1080px) and narrow viewports (<=768px).
- **Verification:** Inspect CSS rules and media queries.

### Task 4: [Testing] Add Automated Regression Tests in `test_dashboard_server.py` [x]
- **Target Files:** `tests/test_dashboard_server.py`
- **Helper Skill:** `test-driven-development`
- **Description:**
  - Add test asserting `#scan-state-pill` is in `<header class="top-meta">` (or top navigation).
  - Add test asserting `app.js` formats heartbeat durations over 60s as minutes.
  - Run full test suites `pytest tests/test_dashboard_server.py` and `pytest tests/test_live_state_language.py`.
- **Verification:** `python -m pytest -q tests/test_dashboard_server.py tests/test_live_state_language.py`.

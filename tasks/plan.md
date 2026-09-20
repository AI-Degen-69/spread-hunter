# Execution Plan: Issue #243 — Consolidate live operations statuses and buttons into top nav bar

## Classification & Stack
- **Size Tier:** Standard — 4 files (index.html, styles.css, app.js defensive guard, prototype.js), one layout decision; no new dependency, no schema change.
- **Task Domain:** `[Design/UI]` primary + `[Frontend/Logic]` (defensive guard only).
- **Stack:** Python 3.11+, FastAPI dashboard server, Vanilla JS + HTML5 + CSS3, pytest + node JS harnesses (`pytest.ini`: testpaths=tests).

## Context & Problem
The dashboard opens with a large `#live-ops-master-card` hero (`index.html:107`) duplicating what the top nav (`header`, `index.html:58`) partly shows. The operator wants statuses + START/STOP/SYNC in the nav from every tab, freeing space and removing duplicate sources of truth (`#btn-sync` vs `#btn-live-sync` both calling `/api/system/sync`; `#db-mode-badge` vs `#hud-db-mode`). `app.js` finds everything by `getElementById` (app.js:837-842, 870-877, 1064-1152), so moving IDs unchanged keeps behavior identical.

## Interface Contracts (locked before logic)
- HTML IDs moved unchanged and unique: `#master-status-indicator`, `#hud-engine-state`, `#hud-engine-sub`, `#hud-venue-mode`, `#hud-venue-sub`, `#hud-guardrail-state`, `#hud-guardrail-sub`, `#live-ops-pulse-dot`, `#master-status-desc`, `#runtime-last-sync`, `#btn-master-start`, `#btn-master-stop`; single sync `#btn-sync` labeled "SYNC VENUE"; `#db-mode-badge` stays in `.top-meta`.
- Removed: `#hud-db-mode`, `#hud-db-sub` (folded into `#db-mode-badge` title), `#btn-live-sync` (markup + handler).
- JS: `renderServiceCards` null-guards `#hud-db-mode`/`#hud-db-sub`; `dataset.wired` handler pattern untouched for start/stop; control endpoints `/api/system/start|stop|sync` unchanged.
- CSS: new `.live-ops-console` (flex, wrap, gap, touch-min, reduced-motion); shrinkable grid tracks only.
- `prototype.js` `PAGE_LAYOUT[home].selectors`: drop `#live-ops-master-card`, point at console/persisted panels.

---

## Tasks (all 6 done [x] on feat/243-top-nav-console)

### Task 1: [Design/UI] Add top-nav console strip and move status IDs
- **Target Files:** `dashboard/static/index.html` (header ~line 58)
- **Helper Skill:** `frontend-ui-engineering`
- **Description:** Add console row inside `<header>` below `.top-meta`; move the 10 status IDs unchanged from the hero into compact pills/tiles following `.pill`/`.kpi-tile`; keep `#db-mode-badge` in `.top-meta`; keep each ID unique.
- **Verification:** `rg 'id="hud-engine-state"' dashboard/static/index.html` shows one hit inside `<header>`; python HTML slice test for `#db-mode-badge` in `.top-meta`.

### Task 2: [Design/UI] Move run controls, dedupe sync button
- **Target Files:** `dashboard/static/index.html`
- **Helper Skill:** `frontend-ui-engineering`
- **Description:** Move `#btn-master-start`/`#btn-master-stop` markup into the console unchanged; relabel `#btn-sync` to "SYNC VENUE"; delete `#btn-live-sync`; keep `#btn-reset`/`#btn-cancel-all` separate from STOP.
- **Verification:** grep: one `id="btn-sync"`, zero `btn-live-sync` in HTML; contract test `test_frontend_control_surface_is_expected` passes.

### Task 3: [Design/UI] Style console responsive
- **Target Files:** `dashboard/static/styles.css` (near header/`.top-meta` ~line 132)
- **Helper Skill:** `frontend-ui-engineering`
- **Description:** Add `.live-ops-console` with `display:flex; flex-wrap:wrap; gap`; touch-min buttons; reuses pill colors; reduced-motion override; shrinkable tracks only (`minmax(0,1fr)` / `minmax(min(Npx,100%),1fr)`).
- **Verification:** `python -m pytest -q tests/test_dashboard_narrow_viewport.py`; manual resize 1440/768/320 no horizontal scroll.

### Task 4: [Frontend/Logic] Defensive app.js guard + dead-handler cleanup + db-path tooltip
- **Target Files:** `dashboard/static/app.js` (~lines 876-922, 1136-1152)
- **Helper Skill:** `test-driven-development`
- **Description:** Null-guard `#hud-db-mode`/`#hud-db-sub` reads in `renderServiceCards`; delete `#btn-live-sync` click handler; write `status.db_path` filename into `#db-mode-badge` title (keeps registry path visible after hero removal).
- **Verification:** node fake-DOM harness `test_master_stop_button_state_management` passes; grep zero `getElementById('btn-live-sync')` writers without guard.

### Task 5: [Design/UI] Update prototype.js layout map
- **Target Files:** `dashboard/static/prototype.js` (~line 45)
- **Helper Skill:** `frontend-ui-engineering`
- **Description:** Remove/replace `#live-ops-master-card` in `PAGE_LAYOUT[home].selectors` so the rail no longer targets a missing node (skipped-selector fallback must not hide the console).
- **Verification:** `python -m pytest -q tests/test_sidebar_prototype.py`.

### Task 6: [Testing] Regression tests for console move
- **Target Files:** `tests/test_dashboard_server.py` (extend near nav-pill/master-stop tests)
- **Helper Skill:** `test-driven-development`
- **Description:** Assert console IDs live inside `<header>` slice, `#db-mode-badge` still in `.top-meta`, each moved ID appears exactly once, `kpi-grid` still present, no `btn-live-sync`; test fails on pre-change HTML.
- **Verification:** targeted `python -m pytest -q tests/test_dashboard_server.py tests/test_dashboard_narrow_viewport.py tests/test_ts_frontend_contract.py tests/test_sidebar_prototype.py`, then full `python -m pytest -q` (run by agent at build time, never handed to operator).

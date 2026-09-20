# Constraints: Issue #243 — Consolidate live operations statuses and buttons into top nav bar

## Quality & Tests
- Zero regressions: full `python -m pytest -q` must remain green.
- Targeted suites must pass 100%: `tests/test_dashboard_server.py` (nav pill, master-stop harness, milestone8 sections), `tests/test_dashboard_narrow_viewport.py`, `tests/test_ts_frontend_contract.py`, `tests/test_sidebar_prototype.py`.
- Anti-Cheat: strictly forbid skipping tests (`@pytest.mark.skip`), deleting assertions, or bypassing linters. No fake-DOM weakening.
- New/changed behavior needs a test that fails without the change (console presence, no duplicate IDs).

## Frontend & UX Boundaries
- `#db-mode-badge`, `#scan-state-pill`, `#market-scan-pill`, `#usdc-balance` stay inside `<div class="top-meta">` within `<header>` in `dashboard/static/index.html`.
- Moved IDs stay byte-identical: `#master-status-indicator`, `#hud-engine-state`, `#hud-engine-sub`, `#hud-venue-mode`, `#hud-venue-sub`, `#hud-guardrail-state`, `#hud-guardrail-sub`, `#live-ops-pulse-dot`, `#master-status-desc`, `#runtime-last-sync`, `#btn-master-start`, `#btn-master-stop`, `#btn-sync`. Each appears exactly once.
- Removed IDs: `#hud-db-mode`, `#btn-live-sync` (markup + dead handler). `app.js` must null-guard `#hud-db-mode` reads.
- String `kpi-grid` stays present in `index.html`.
- Endpoints unchanged: `/api/system/start`, `/api/system/stop`, `/api/system/sync` (no new calls, no renames).
- Responsive: console uses `display:flex` + `flex-wrap:wrap` + `gap`; any grid track uses `minmax(0,1fr)` or `minmax(min(Npx,100%),1fr)` — no fixed-pixel `minmax` floor; buttons keep `min-height:var(--touch-min)`; motion paired with `@media (prefers-reduced-motion: reduce)`.
- No external JS/CSS dependencies. Keep `#btn-reset` / `#btn-cancel-all` visually separate from STOP RUN.

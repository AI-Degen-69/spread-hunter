# SPEC: Issue #243 — Consolidate live operations statuses and buttons into top nav bar

## Goal
Move live statuses and run controls into a top-nav console strip so the big `#live-ops-master-card` hero can be removed, freeing vertical space, with zero behavior change.

## Acceptance criteria (from issue)
- [ ] Engine, venue, watchdog, and registry status visible in the top nav from every tab without scrolling.
- [ ] START RUN, STOP RUN, SYNC VENUE work from the nav console with the same behavior as before.
- [ ] Old hero card removed/collapsed with no orphaned IDs or dead buttons.
- [ ] `python -m pytest -q` passes.

## Scope
### In scope
- New console strip inside `<header>` below `.top-meta` carrying (IDs unchanged): `#master-status-indicator`, `#hud-engine-state`, `#hud-engine-sub`, `#hud-venue-mode`, `#hud-venue-sub`, `#hud-guardrail-state`, `#hud-guardrail-sub`, `#live-ops-pulse-dot`, `#master-status-desc`, `#runtime-last-sync`, plus `#btn-master-start`, `#btn-master-stop`, and single sync `#btn-sync` relabeled "SYNC VENUE".
- Dedupe: keep `#db-mode-badge` (in `.top-meta`) as the single registry indicator; drop `#hud-db-mode`. Keep `#btn-sync`; remove `#btn-live-sync`.
- Responsive console styling (flex wrap, shrinkable tracks, touch targets, reduced-motion).
- Defensive `app.js` null-guard for removed `#hud-db-mode`; remove dead `#btn-live-sync` handler; carry `#hud-db-sub` db filename into `#db-mode-badge` title tooltip.
- Update `prototype.js` `PAGE_LAYOUT` home selectors (remove `#live-ops-master-card`).

### Out of scope (per issue)
- Start/stop/sync backend logic, polling cadence, guardrail rules; visual move only.

## Edge cases
- `#db-mode-badge` must stay inside the `<div class="top-meta">`…`</header>` slice (`test_scan_state_pill_in_top_nav_bar`).
- Every moved ID must be unique in the page (`app.js` uses `getElementById`).
- String `kpi-grid` must remain in `index.html` (`test_milestone8_html_contains_required_sections`).
- IDs `btn-master-stop` / `master-status-indicator` keep working with the fake-DOM harness (`test_master_stop_button_state_management`).
- Control endpoints unchanged: `/api/system/start`, `/api/system/stop`, `/api/system/sync` (`test_frontend_control_surface_is_expected`).
- No fixed-pixel `minmax(Npx,…)` grid floors (`test_dashboard_narrow_viewport.py`).

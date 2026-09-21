# Plan — Issue #264: Dashboard input lag (tab clicks freeze 2-4s)

## Spec (concise — Small tier)
**Goal:** tab clicks and control clicks respond immediately, even while the 2s poll batch is in flight.
**Approach:** (1) render only the visible tab each poll, rendering a tab on switch instead of every poll;
(2) yield the heavy chart renders so input is handled between chunks; (3) keep the existing
`isPolling` overlap guard intact.
**Out of scope:** strategy/quoting/sizing, endpoint contracts, visual redesign, backend changes
unless measured proof shows the backend blocks.
**Interfaces:** none change — same 7 endpoints, same payloads, same DOM ids.

## Task type & size
- **Type:** Performance (primary). **Size:** Small — one file (`dashboard/static/app.js`), scheduling-only change.

## Tasks

### T1 — Confirm the blocker [Performance]
- **Target files:** `dashboard/static/app.js` (`pollStatus` :5125, render chain :5176-5186, timers :5214-5217)
- **Build:** read the poll→render path and note which renders run unconditionally each 2s poll
  (all four sections incl. hidden tabs + Monte Carlo/KDE/markout charts), establishing the long-task hypothesis.
- **Helper skill:** `performance-optimization` (measure-first)
- **Verification:** hypothesis recorded here; no code change.

### T2 — Render only the visible tab per poll [Performance]
- **Target files:** `dashboard/static/app.js` (`pollStatus`, `switchTab` :373)
- **Build:** gate each section render (`renderKPIs`/`renderMarkets`/`renderOrdersTrades`/`renderScreener`)
  on its tab's visibility; render the newly shown tab inside `switchTab` so a switch paints
  synchronously from the latest data without waiting for the next poll.
- **Helper skill:** `test-driven-development`
- **Verification:** new pytest static test (RED→GREEN) asserting hidden-tab renders are gated;
  focused suite green.

### T3 — Yield heavy chart renders to input [Performance]
- **Target files:** `dashboard/static/app.js` (chart renders: Monte Carlo :2497, KDE :2377, markout :2688)
- **Build:** defer off-critical chart work past paint (`requestAnimationFrame`/chunked) so a click
  arriving mid-render is handled between chunks; keep `isPolling` overlap guard behavior unchanged.
- **Helper skill:** `test-driven-development`
- **Verification:** pytest static test pinning the deferral (RED→GREEN); manual rapid-click check shows no queued replay.

### T4 — Focused verification [Performance]
- **Target files:** `tests/` (new test from T2/T3) + existing dashboard selection
- **Build:** run `python -m pytest -q tests/test_dashboard_server.py tests/test_dashboard_snapshot_cache.py tests/test_dashboard_narrow_viewport.py`
  plus the new test; hands-on: open `http://127.0.0.1:8799`, click tabs rapidly mid-poll, confirm instant switch.
- **Helper skill:** `incremental-implementation`
- **Verification:** all focused tests green; hands-on tab-click check passes (reported, not delegated).

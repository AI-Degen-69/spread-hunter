# Plan — #473 [VALIDATE] Step 1 - Base shadow rehearsal (end-to-end, no signer)

Branch: i473/base-shadow-rehearsal | Issue: #473
Size: Standard | Type: Code (Frontend/UI + Logic) + Validation
Stack: Python 3.11+ (pytest), Node.js (CommonJS test harness), Vanilla JS
Labels: ready-for-agent

## CodeRabbit plan intake

- **Adopted:**
  - Master START disable guard in `dashboard/static/app.js` when `!lastDbIsProduction` or `status?.db_is_production !== true`.
  - Tooltip `'Disabled in SHADOW view'` with opacity `0.45` and cursor `not-allowed`.
  - Prevent click POST to `/api/system/start` with alert when in non-production/shadow view.
  - Extend `tests/js/start_toggle_harness.cjs` and `tests/test_dashboard_server.py` with regression assertions.
  - Base shadow rehearsal command: `python -m core_brain.shadow_run --minutes 10 --db data\val_step1.db --run-id val-step1` with observer.
- **Rejected:** None.
- **[UNVERIFIED] resolved from code:**
  - `lastDbIsProduction` is maintained in `renderDbMode(status)`. `renderServiceHeader` checks both `status?.db_is_production === true` and `lastDbIsProduction` to ensure robustness against call ordering.
  - Harness `tests/js/start_toggle_harness.cjs` needs `#btn-master-toggle` click capture added to its stub DOM.
  - `runtime/markets.json` is missing in the new worktree and will be seeded from the main project root before rehearsal.

## Locked constraints (CONSTRAINTS.md)

- Zero regressions: `tests/test_dashboard_server.py`, `tests/test_service_toggles.py`, `tests/test_live_state_language.py` remain green.
- Node harness check passes: `node tests/js/start_toggle_harness.cjs dashboard/static/app.js`.
- Anti-cheat: no skipping/disabling tests, no deleting assertions.
- Safety: zero signer loaded; no real trades or live venue requests; rehearsal only.
- Database isolation: never write to or delete `data/orders.db`. Rehearsal is strictly isolated to `data/val_step1.db`.

## Dependency graph

```
T1 (frontend master START guard in app.js) ──> T2 (harness & python regression tests)
                                                        │
                                                        ▼
                                               T3 (10m shadow rehearsal & verification)
```

## Tasks

- [x] **T1** [S] [Frontend/UI] — Disable master START in non-production/shadow view in `dashboard/static/app.js`.
  - Target files: `dashboard/static/app.js`
  - Changes: In `renderServiceHeader`, check if production database is active. When not active, disable master START button, set opacity to `0.45`, cursor to `not-allowed`, and set descriptive tooltip. In master toggle click handler, return early with alert if not production, preventing any POST to `/api/system/start`.
  - Helper skill: `frontend-ui-engineering`
  - Depends on: None
  - Verification: Manual inspection of DOM styles / attributes.

- [x] **T2** [S] [Backend/Logic + Test] — Regression test coverage for master START button.
  - Target files: `tests/js/start_toggle_harness.cjs`, `tests/test_dashboard_server.py`
  - Changes: Extend harness to capture `#btn-master-toggle` element and its click listener. Add assertions for disabled state in SHADOW view and enabled state in LIVE view. Add pytest test cases `test_master_start_disabled_on_non_production_view` and `test_master_start_click_on_shadow_view_posts_nothing`.
  - Helper skill: `test-driven-development`
  - Depends on: T1
  - Verification: `python -m pytest -q tests/test_dashboard_server.py -k "master_start"` and `node tests/js/start_toggle_harness.cjs dashboard/static/app.js`.

- [ ] **T3** [M] [Validation] — Execute 10-minute base shadow rehearsal and verify loop.
  - Target files: Runtime data files (`runtime/markets.json`, `data/val_step1.db`, `reports/`)
  - Changes: Seed `runtime/markets.json`. Run `python -m core_brain.shadow_run --minutes 10 --db data\val_step1.db --run-id val-step1` alongside `statistics_observer`. Check console banner for "NO SIGNER LOADED". Switch dashboard to `data\val_step1.db`, verify SHADOW badge and disabled START button. Verify resting orders, simulated fills/closes in dashboard and observer report.
  - Helper skill: `incremental-implementation`
  - Depends on: T1, T2
  - Verification: Review terminal logs, dashboard UI, and generated report file.

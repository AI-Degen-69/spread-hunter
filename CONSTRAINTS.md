# CONSTRAINTS — #586 (locked by Station II, enforced through Station V)

Governs the `i586/unify-top-nav-pill-grammar-across-header` branch.

## Scope & File Boundaries

- **Target files**:
  - `dashboard/static/index.html` — dropdown fallback rows adopt label/value spans (match header pill markup); switcher container unchanged.
  - `dashboard/static/styles.css` — mode-tint refactor (text color = state only), dropdown grid columns, meta ellipsis, panel width 340px → 380px, demo-row pill styles.
  - `dashboard/static/app.js` — `renderServicesDropdown` (renderPillHtml pills, fixed meta vocabulary, tooltip move), `renderRunSwitcher` + `demoSwitcherRow` (pill markup), `renderDbMode` (tint-only change, no logic change).
  - `tests/test_ui_top_nav_consistency.py` — new suite pinning label/value/detail roles, meta vocabulary, grid columns, tooltip relocation, dot separation.
  - `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- **Protected files (MUST NOT BE MODIFIED)**:
  - `dashboard/server.py` — backend server remains untouched.
  - `server.ts` — TypeScript server entrypoint remains untouched.
  - `dashboard/static/prototype.js` and `prototype.css` — prototype logic and styling preserved.
  - `data/orders.db` — production registry is strictly read-only.
  - `tests/test_ui_top_nav_groups.py` — existing #504 coverage preserved as-is; no edits, only run.
  - State computation: `stateKey`, `processState`, `aggregateServiceState`, `dbModeVerdict`, `isShadowMode` — logic untouched.
  - Panel placement/dismissal: `placePanel`, `placeRunSwitcher`, outside-click/Escape handlers — untouched.

## Quality Guardrails

1. **Zero Regressions**:
   - `python -m pytest tests/test_ui_top_nav_consistency.py tests/test_ui_top_nav_groups.py tests/test_ui_278_status_strip.py tests/test_ui_277_touch_targets.py tests/test_sidebar_prototype.py -q` MUST pass.
   - `python -m pytest tests/ -k "live_state or dashboard" -q` MUST pass.
2. **Touch Targets & Accessibility**:
   - All interactive controls MUST maintain `>= 44px` touch targets (#277).
   - `.top-meta` MUST preserve `tabindex="0"` (#278).
   - Row `title` tooltips MUST carry the relocated live-only/shadow-only explanations.
3. **Anti-Cheat Rules**:
   - No disabled or skipped tests.
   - No deleted assertions; existing `test_ui_top_nav_groups.py` intentions preserved verbatim.
   - No linting or typing suppressions.
   - Zero new external runtime dependencies.

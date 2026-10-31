# CONSTRAINTS.md — Issue #867: Redesign Dashboard Layout and Theme

Branch: `i867/redesign-dashboard-layout-and-theme` | Issue: #867

Locked during Station II planning. Stations III–V must hold every line below.

## Zero Regressions

- Targeted test suites covering all touched files must pass:
  - `python -m pytest -q tests/test_ui_top_nav_groups.py tests/test_ui_top_nav_consistency.py tests/test_ui_278_status_strip.py tests/test_ui_277_touch_targets.py tests/test_live_state_language.py tests/test_dashboard_run_switcher.py tests/test_kanban_lanes.py tests/test_sidebar_prototype.py tests/test_dashboard_narrow_viewport.py`
- Existing grammar and class-list contracts in `test_ui_top_nav_groups.py` and `test_ui_top_nav_consistency.py` (`test_header_status_pills_share_markup`, `test_profile_badge_renders_states`, `test_switcher_rows_use_pill_grammar`, `test_tab_row_styling`, `test_pill_value_css`, `test_superseded_top_nav_pills_hidden`) must pass without weakening assertions.
- In `prototype.css`, edit existing rules in place — never append duplicate selectors (`test_sidebar_prototype.py`'s `_rule()` helper inspects the first matching selector block).

## Scope Fences (Hard)

- **Only these files may be modified:**
  - `dashboard/static/index.html`
  - `dashboard/static/styles.css`
  - `dashboard/static/prototype.css`
  - `dashboard/static/app.js` (strictly limited to `renderDbMode`, `closeRunSwitcher`, `runSwitcherLabel`, `profileOptionsHtml`/`armRowHtml`/`presetRowHtml`/`renderProfileOptions`, and `setKanbanLane`)
  - `tests/js/live_state_harness.cjs`
  - `tests/test_ui_top_nav_groups.py`
  - `tests/test_live_state_language.py`
  - `tests/test_kanban_lanes.py`
  - `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`
- **Must NOT be modified:**
  - `dashboard/server.py`, `dashboard/preflight.py`, `core_brain/**`, `scripts/**`
  - `dashboard/static/prototype.js`, `dashboard/static/analysis_v2.css`, `dashboard/static/analysis_v2.js`
  - `data/orders.db`, `data/demo_orders.db`

## Anti-Cheat & Safety Rules

- Never skip or disable tests, delete existing assertions, or suppress linters.
- Never run live-order commands (`quote`, `complete`, `trader_loop`, `dashboard/preflight.py`, or pressing START on the production database).
- No new external dependencies.

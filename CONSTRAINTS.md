# CONSTRAINTS — #504 (locked by Station II, enforced through Station V)

Governs the `i504/improve-reorganize-the-top-nav-bar-with-consistent` branch.

## Scope & File Boundaries

- **Target files**:
  - `dashboard/static/index.html` — group wrappers, pill markup normalization, button classes, services dropdown location.
  - `dashboard/static/styles.css` — `.top-group` flex rules, `.pill-value` styling, `.ctrl-btn` base and variants, `.header-panel` positioning rules, kanban hero neutral styling.
  - `dashboard/static/app.js` — `renderPillHtml` helper, master toggle class helper, `placePanel` positioning, outside click & Escape handlers, aggregate state helper.
  - `tests/test_ui_top_nav_groups.py` — comprehensive test suite covering header groups, pill grammar, button hierarchy, and panel anchoring.
  - `tests/test_dashboard_server.py` — update top-meta group assertion to child group assertions; replace exact button `className ==` checks with token assertions.
  - `tests/test_live_state_language.py` — update elsewhere pill exact string check to token checks.
  - `tests/js/live_state_harness.cjs`, `tests/js/master_toggle_harness.cjs`, `tests/js/start_toggle_harness.cjs` — update fake DOM `textContent` derivation from `innerHTML`.
  - `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.

- **Protected files (MUST NOT BE MODIFIED)**:
  - `dashboard/server.py` — backend server remains untouched.
  - `server.ts` — TypeScript server entrypoint remains untouched.
  - `dashboard/static/prototype.js` and `prototype.css` — prototype logic and styling preserved.
  - `data/orders.db` — production registry is strictly read-only.
  - Existing test suites outside the touched modules.

## Quality Guardrails

1. **Zero Regressions**:
   - `python -m pytest tests/test_ui_top_nav_groups.py tests/test_ui_278_status_strip.py tests/test_ui_277_touch_targets.py tests/test_sidebar_prototype.py -q` MUST pass.
   - `python -m pytest tests/ -k "live_state or input_lag or analytics_surface or start_toggle or dashboard" -q` MUST pass.
   - `npm run build` MUST pass cleanly.

2. **Touch Targets & Accessibility**:
   - All interactive controls (buttons, triggers) MUST maintain `>= 44px` touch targets (`--touch-min` / #277).
   - `.top-meta` MUST preserve `tabindex="0"` for keyboard scrolling (#278).
   - Groups MUST have explicit `role="group"` and `aria-label`s.

3. **Anti-Cheat Rules**:
   - No disabled or skipped tests (`@pytest.mark.skip`, `xit`, `it.skip`).
   - No deleted assertions; existing test intentions must be preserved.
   - No linting or typing suppressions added to bypass checks.
   - Zero new external runtime dependencies added to `package.json` or `pyproject.toml`.

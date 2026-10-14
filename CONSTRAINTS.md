# CONSTRAINTS — #558 (locked by Station II, enforced through Station V)

Governs the `spread-hunter_i558-561_taxonomi` branch only. The #564 contract
this file replaces is branch-scoped by its own header line and stays readable
in git history.

## Scope & File Boundaries

- New files (exactly two, plus this planning set):
  - `core_brain/close_methods.py` — the canonical taxonomy; stdlib only,
    `from __future__ import annotations`, no `core_brain` imports.
  - `tests/test_close_method_taxonomy.py` — the guard; imports only
    `core_brain.close_methods`.
  - `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - Every consumer — `core_brain/kpi.py`, `statistical_analytics.py`,
    `registry_state.py`, `ladder.py`, `market_resolution.py`,
    `order_registry.py`, `paired_shadow.py`, `shadow_exec.py`,
    `single_buy_saver.py`, `run_scorer.py`, all of
    `statistical_validation_run/`, every `scripts/` file, and
    `dashboard/` — those moves are #559/#560's scope. The guard FAILS on
    them by design; do not "fix" them here.
  - `data/orders.db` and every other store — read-only throughout.
  - Every existing test file.

## The guard is RED at T1 — by design

- Issue #558's verify line: "RED before the consumers move, GREEN after".
  Operator decision (this session): the scan asserts ZERO stray method
  lists. It is red at T1 on the current sites and clears only at #560.
- This is not a regression, not a bug, and not something to pin, allowlist,
  xfail, or scope-expand away. Station IV stops before merge and hands the
  red PR to the owner; T1 and #559 merge by owner override; main is red
  until #560 lands.
- Everything else in the new test file (pins, scanner self-checks) must be
  GREEN at T1.

## Quality bar

- Zero regressions: the module is new (no importers yet); no existing test
  is touched. The focused run `python -m pytest -q
  tests/test_close_method_taxonomy.py` is expected RED (scan only); pins
  and self-checks GREEN.
- The test file cannot import without the module — RED first by
  construction.
- Anti-cheat: no skipped or disabled tests, no deleted assertions, no
  linter suppression, no baseline pin of current violations, no new
  external dependencies.
- Naming/typing (docs/agents/python-conventions.md): SCREAMING_SNAKE_CASE
  constants, PascalCase record, type hints and docstrings on the module's
  public surface, absolute repo-rooted imports, `from __future__ import
  annotations`.

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

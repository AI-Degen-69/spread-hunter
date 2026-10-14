# CONSTRAINTS — #564 (locked by Station II, enforced through Station V)

Governs the `i564/command-palette-lists-sidebar-links-with-their-rai` branch
only. The #562 contract this file replaces is branch-scoped by its own header
line and stays readable in git history.

## Scope & File Boundaries

- Target files (exactly four, plus this planning set):
  - `dashboard/static/command-palette.js` — link entries in `buildEntries`,
    icon clone in `makeRow`; `window.open` new-tab `noopener`; every DOM read
    guarded so pages without rail markup stay inert.
  - `dashboard/static/styles.css` — row-icon sizing rule only (16px,
    `currentColor`, aligned with the label); no layout, no new tokens.
  - `tests/js/command_palette_harness.cjs` — link fixture (href + svg child),
    `makeEl.querySelector('svg')`, `win.open` spy with `openedLinks` observation.
  - `tests/test_dashboard_command_palette.py` — new assertions only, plus the
    documented repin of the exclusion test to inclusion.
  - `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - `dashboard/static/prototype.js`, `dashboard/static/app.js` — the rail owns
    its items; the palette reads, never writes.
  - `dashboard/static/index.html` — no markup change needed (overlay already
    renders rows generically).
  - Every existing test file outside the palette suite.
  - `data/orders.db` — production registry, read-only at all times.

## Quality bar

- Zero regressions: `python -m pytest -q tests/test_dashboard_command_palette.py`
  plus the dashboard `-k` selection
  (`live_state or input_lag or analytics_surface or start_toggle or dashboard`).
  The full sweep is GitHub CI's merge gate, never a local step.
- Every new behaviour carries a test that fails without the change (RED first,
  via stash of the product file). The repinned exclusion test is a deliberate
  expectation change — recorded here so no station mistakes it for a regression.
- Anti-cheat: no skipped or disabled tests, no deleted assertions, no linter
  suppression. No new external dependencies.
- Accessibility floor (binding): cloned svgs stay `aria-hidden` (they already
  are); rows keep `role="option"` + label text; link rows expose their URL via
  `title` or `sub` so a screen reader user knows Enter leaves the page. Never cut.

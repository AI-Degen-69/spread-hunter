# CONSTRAINTS — #477 (locked by Station II, enforced through Station V)

Governs the `spread-hunter-i477` branch only. The #531 contract this file replaces is
branch-scoped by its own header line and stays readable in git history.

## Scope & File Boundaries

- Target files (exactly five, plus this planning set):
  - `dashboard/static/index.html` — the hidden `#command-palette` overlay beside
    `#reset-modal` (`index.html:839-848` pattern) and
    `<script src="/static/command-palette.js?v=1">` after `prototype.js` (`:864`),
    no `async`/`defer`.
  - `dashboard/static/styles.css` — `.modal-*` rules (`:731-775`) reused unchanged;
    new `palette-` rules only (list scroll, group heading, rows, disabled row,
    highlighted row), `z-index:1000`.
  - `dashboard/static/command-palette.js` (NEW) — the whole palette.
  - `tests/js/command_palette_harness.cjs` (NEW) — offline harness.
  - `tests/test_dashboard_command_palette.py` (NEW) — pytest wrapper.
  - `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - `dashboard/static/app.js` — 446 KB, 9,386 lines, and many harnesses evaluate it;
    the palette reuses its seams (listeners, snapshots, buttons) and edits none.
  - `dashboard/static/prototype.js` — `showPage` stays private; the palette clicks.
  - `server.ts`, every Python file, every existing test file.
  - `data/orders.db` — production registry, read-only at all times.

## Quality bar

- Zero regressions: the issue's own selection passes unchanged —
  `pytest tests/ -k "live_state or input_lag or analytics_surface or start_toggle or
  dashboard"` (covers `test_dashboard_server.py`, `test_dashboard_input_lag.py`,
  `test_live_state_language.py`, `test_analytics_surface_mount.py`,
  `test_sidebar_prototype.py` including the script-order assertion at `:348`).
  The full sweep is GitHub CI's merge gate, never a local step.
- Every new behaviour carries a test that fails without the change (RED first); the
  new pytest file's name contains "dashboard" so the issue's `-k` selection runs it.
- Anti-cheat: no skipped or disabled tests, no deleted assertions, no linter
  suppression. No new external dependencies.
- Accessibility floor (from the issue, binding): `role="dialog"`, `aria-modal="true"`,
  visible title, focus trap while open, focus restore on close, combobox pattern for
  the filterable list, arrow-key selection, Enter activation. Never cut.

## Environment quirks (verified at intake)

- This host's shell sometimes exports `PORT=0`, which breaks dashboard test
  collection — run pytest with `unset PORT;` first when set. Planning session: clean.
- Proven command (cwd = worktree, 44 passed / 1.85 s):
  `"C:/Users/Tiger/Agents/Projects/AI Trading/spread-hunter/.venv/Scripts/python.exe" -m pytest tests/test_sidebar_prototype.py -q`
- No `tsconfig.json` and `package.json` is `"type": "module"`, so
  `npx -y -p typescript@5.7.3 tsc --noEmit server.ts` fails on `main` with exactly one
  pre-existing error — `server.ts(251,111): error TS1343` (`import.meta`). Acceptable
  result after the change: the SAME single error, never fewer — and never an edit to
  `server.ts`.
- Node is present (hermes toolchain, 26.7.0), so harness tests must NOT skip —
  a green run with skips proves nothing about the palette.

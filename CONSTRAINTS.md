# CONSTRAINTS — #562 (locked by Station II, enforced through Station V)

Governs the `i562/command-palette-hotkey-loses-to-the-browser-and-pa` branch
only. The #477 contract this file replaces is branch-scoped by its own header
line and stays readable in git history.

## Scope & File Boundaries

- Target files (exactly five, plus this planning set):
  - `dashboard/static/index.html` — one trigger button
    (`#btn-command-palette`) in `.top-meta` beside RESET (`:97-98` pattern),
    and the hint line (`:863`) naming Ctrl+K and `/`. Markup only.
  - `dashboard/static/command-palette.js` — bind the trigger by id in
    `initPalette` (`:422-435`); no other behavior change.
  - `dashboard/static/styles.css` — one minimal button rule reusing the
    existing pill/button tokens; no new palette, no layout.
  - `tests/js/command_palette_harness.cjs` — fixture trigger element; the
    harness already supports `{type:'click', target}` and records
    `keyPrevented`.
  - `tests/test_dashboard_command_palette.py` — new assertions only.
  - `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - `dashboard/static/app.js`, `dashboard/static/prototype.js` — seams reused,
    none edited (carried over from #477).
  - `dashboard/server.py` — serving verified correct (content-hash
    fingerprints + no-store HTML); the stale-page cause is operational, not code.
  - Every existing test file outside the palette suite.
  - `data/orders.db` — production registry, read-only at all times.

## Quality bar

- Zero regressions: the palette suite plus the issue's dashboard selection pass
  unchanged —
  `python -m pytest -q tests/test_dashboard_command_palette.py` and
  `pytest tests/ -k "live_state or input_lag or analytics_surface or start_toggle or
  dashboard"`. The full sweep is GitHub CI's merge gate, never a local step.
  (Verified at intake on `origin/main`: 26/27 green in a subset extraction; the
  one failure needs `dashboard.server` from the full checkout, 27/27 in PR #561.)
- Every new behaviour carries a test that fails without the change (RED first).
- T1 is evidence before synthesis: no hotkey code is touched until the served
  bytes prove whether the palette ships to the operator's browser at all.
- Anti-cheat: no skipped or disabled tests, no deleted assertions, no linter
  suppression. No new external dependencies.
- Accessibility floor (binding): the trigger is a real `<button>` with an
  accessible name (magnifier `aria-hidden`, visible label or `aria-label`);
  focus moves into search on open and returns to the trigger on close. Never cut.

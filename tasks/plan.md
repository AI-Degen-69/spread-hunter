# Plan — #562: Command palette hotkey loses to the browser, no mouse entry point

Branch: i562/command-palette-hotkey-loses-to-the-browser-and-pa | Issue: #562

## Why (issue summary)

The #477 palette ships exactly two bindings (Ctrl/Cmd+K, `/`), but live in
Chrome Ctrl+K focuses the URL bar and `/` does nothing — and no mouse control
opens the palette. The offline harness passes, so the failure is in the
live-serving path, not palette logic.

## Tier: Standard (5 files, one Debug decision + one small UI addition)

Two debug/UI tasks plus a test task; no new dependency, no API, no schema.

## Type: Debug + Design/UI

- [Debug] T1 — serving-source diagnosis, live reproduction.
- [Design/UI] T2 — nav trigger button + hint copy.
- [Code] T3 — harness regression tests (RED first).

## CodeRabbit plan intake (2026-10-14)

No plan comment had landed at planning time (only this station's `@coderabbitai
plan` request). Proceeding from code; Station III must not wait for it. If a
plan lands mid-build, mine it for test cases only — the diagnosis in T1 is
already grounded in fresher evidence (serving stack predates #561).

## Resolved questions (from code + operator, 2026-10-14)

- "Ctrl+\" was `/` (operator-confirmed). No backslash work exists or is added.
- Stale-browser-cache ruled OUT: no-store HTML + content-hash fingerprints
  (`server.py:3994-4008,4062-4082`). Prime suspect is operational: the
  serving checkout sits pre-#561, so the page has no palette markup and
  `initPalette` returns before registering the keydown listener
  (`command-palette.js:425-434`) — one cause for both dead hotkeys.
- Button placement default stands: minimal trigger in `.top-meta`; #504 relocates.

## Improvement proposal: none — evidence reshaped T1 instead

No filler proposal. The one evidence-backed change to the issue's approach:
do NOT touch hotkey code until T1 proves what the operator's browser actually
receives (served-bytes grep + live keypress). If T1 shows the palette ships
and keys still fail, T1's findings become the bug's new root cause and T2
follows unchanged.

## Dependency graph

- T1: no dependencies (risk-first: kills or confirms the operational theory
  before any code moves).
- T2: depends on T1 (the button must open a palette T1 proved live).
- T3: depends on T2 (tests pin T2's wiring; RED run first against unmodified code).

## Tasks

### T1 [Debug] Prove the served page, reproduce live — S

- Target files: none (reads only) — dashboard URL `/`, serving checkout/process.
- What: fetch the served `/` bytes and grep for `command-palette`; identify
  which checkout and process serves the operator's dashboard; if stale,
  pull/restart through the operator-approved stack path and re-fetch. Then
  press Ctrl+K and `/` in live Chrome and record which opens the palette.
- Skill: `debugging-and-error-recovery` (reproduce → localize → fix root cause).
- Verify: served-bytes grep green AND live open observed for both hotkeys, or
  a written root-cause finding that redirects T2. Checkpoint: **report the
  diagnosis before touching code.**

### T2 [Design/UI] Nav trigger button + hint names the hotkeys — M

- Target files: `dashboard/static/index.html` (button in `.top-meta` beside
  RESET, hint line `:863`), `dashboard/static/command-palette.js` (bind by id
  in `initPalette`), `dashboard/static/styles.css` (one token-reusing rule).
- What: real `<button id="btn-command-palette">` with magnifier icon
  (`aria-hidden`) + accessible name; click opens the palette with focus in
  search; close returns focus to the button via the existing opener path
  (`openPalette` captures `doc.activeElement`, `:335`). Hint becomes e.g.
  "Ctrl+K or / to open. Arrows to move. Enter to choose. Escape to close."
- Skill: `frontend-ui-engineering` (native button, focus management, tokens).
- Depends on: T1. Verify: live click opens + harness click test (T3) green.

### T3 [Code] Harness regression tests, RED first — S

- Target files: `tests/js/command_palette_harness.cjs` (fixture trigger
  element), `tests/test_dashboard_command_palette.py` (new assertions only).
- What: RED run first (trigger click with no binding → palette stays shut;
  Ctrl+K asserts `keyPrevented` true and open), then T2 makes them GREEN.
  Keep: no skips (node present), no touched existing assertions.
- Depends on: T2. Verify:
  `python -m pytest -q tests/test_dashboard_command_palette.py` green, then the
  dashboard `-k` selection from CONSTRAINTS.md.

## Checkpoints

1. After T1: one-line diagnosis report (serving source + live result). If the
   cause is operational and fixed by restart/pull, T2 still builds the button.
2. After T3: suite green, branch ready for Station IV.

## Build status (Station III)

- [x] T1 — diagnosis proven: operator stack serves pre-#561 page, no code defect
- [x] T2 — trigger button + hint committed
- [x] T3 — 31 passed palette suite + 387 dashboard selection + served-bytes proof

# Plan — #564: Command palette lists sidebar links with their rail icons

Branch: i564/command-palette-lists-sidebar-links-with-their-rai | Issue: #564

## Why (issue summary)

The palette mirrors the rail but drops its four links and all its icons:
`buildEntries` selects only `.proto-nav-btn[data-page]`, and rows render
text-only. Every rail item already carries its icon (`buildIcon`,
`prototype.js:239-251`), and links behave as new-tab anchors — so the palette
can read both from the DOM with no hardcoded lists.

## Tier: Standard (4 files, one DOM-driven addition + harness infra)

## Type: Code + Design/UI

- [Code] T1 — link entries + icon cloning in production code.
- [Code] T2 — harness infra + tests RED-first + repin.
- [Design/UI] T3 — live browser proof + suite green.

## CodeRabbit plan intake (2026-10-14)

No plan comment had landed at planning time (only this station's `@coderabbitai
plan` request). Proceeding from code; Station III must not wait for it.

## Resolved questions (from code, 2026-10-14)

- Icons: clone each rail item's own `<svg>` (aria-hidden, 16x16, currentColor).
- Exclusion test repin is a deliberate expectation change (recorded in
  CONSTRAINTS.md), not a regression.
- Harness needs `makeEl.querySelector('svg')` + `win.open` spy; production
  guards both with `typeof` checks.

## Improvement proposal: none — the minimal fit is already the issue's approach

No separate proposal. Cloning the rail's own nodes (rather than copying path
data into the palette) is the smallest change that tracks future rail edits
for free; anything else would be a second source of truth.

## Dependency graph

- T1: no dependencies (risk-first: the DOM approach is the uncertain core).
- T2: depends on T1 (tests pin T1's wiring; RED proven by stashing T1).
- T3: depends on T2 (live proof runs against tested code).

## Tasks

### T1 [Code] Link entries + row icons in production code — M

- Target files: `dashboard/static/command-palette.js`, `dashboard/static/styles.css`.
- What: in `buildEntries`, after the pages loop, query `.proto-nav-btn`
  without `data-page` but with `href` → entries `{id:'link:'+label, label,
  group:'Links', sub: href, icon: cloned svg or null, run: close palette, then
  window.open(href, '_blank', 'noopener')}`. Icon helper: guarded
  `btn.querySelector('svg')` → `cloneNode(true)` (guard `typeof` on both).
  `makeRow` prepends the clone in a `.palette-icon` span (skip when null).
  CSS: `.palette-row .palette-icon svg { width:16px; height:16px; }` aligned
  with the label; reuse existing tokens only.
- Skill: `frontend-ui-engineering` (accessible rows, token reuse).
- Depends on: none. Verify: T2 tests + T3 live proof.

### T2 [Code] Harness infra + tests, RED first — S

- Target files: `tests/js/command_palette_harness.cjs`,
  `tests/test_dashboard_command_palette.py`.
- What: fixture link elements (href + svg child, reuse the `externalLink`
  flag to mean "one external link present"); `makeEl.querySelector('svg')`
  (search children recursively, `null` when absent); `win.open` spy recording
  `{href, target, features}` into a new `openedLinks` observation.
  New tests: link rows listed with icons; Enter on a link records the
  new-tab open (href + `_blank`); page rows carry icons; guard (no svg → row
  still renders). REPIN `test_palette_lists_six_pages_and_two_actions` to
  inclusion (documented expectation change).
  RED: stash the T1 product files, new tests fail; pop, GREEN.
- Depends on: T1. Verify: `python -m pytest -q
  tests/test_dashboard_command_palette.py` green.

### T3 [Design/UI] Live proof + suite green — S

- Target files: none (reads) + rerun suites.
- What: scratch server from the branch (read-only demo db, free port);
  single batched eval: rows contain svg icons; stub `window.open`, activate a
  link row, assert captured `(href, '_blank')`; console + requests clean.
  Then the dashboard `-k` selection green.
- Depends on: T2. Verify: eval JSON all-true + suites green.
- Checkpoint: **all green → Station IV.**

## Checkpoints

1. After T2: suite green with repin recorded, no other test touched.
2. After T3: live proof JSON + suites green, branch ready for Station IV.

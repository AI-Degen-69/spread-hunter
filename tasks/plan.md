# Plan — #477: Command palette for pages, actions, and market search

Branch: `spread-hunter-i477` | Issue: [#477](https://github.com/AI-Degen-69/spread-hunter/issues/477) | Tier: Standard | Type: UX/UI (keyboard-first dashboard feature)

## Why (issue summary)

Add a command palette (Cmd/Ctrl+K, `/` alias) so the operator can jump to any sidebar
page, trigger SYNC VENUE, open the existing RESET typed-confirm, and search markets
already loaded in the page — all keyboard-first. Vanilla JS, reuses the existing modal
styling, no endpoints, no dependencies.

## CodeRabbit plan intake (read once, costed once)

- **Adopted:** separate `dashboard/static/command-palette.js` loaded after
  `prototype.js` (keeps the 446 KB `app.js` and its many harnesses untouched —
  `tests/test_sidebar_prototype.py:348` asserts only `app.js` before `prototype.js`);
  click-the-sidebar-button navigation (the real click runs persistence,
  `aria-current`, drawer close); capture-phase keydown + `stopPropagation` so the
  kanban arrow listener (`app.js:9120-9128`) and reset-Escape listener
  (`app.js:776-780`) stay silent while open; three-source market search with dedupe
  by condition id.
- **Rejected:** its Task 4 is closeout, not a build task — folded into Verification
  below. Its "five pages" test assertion — live `PAGE_LAYOUT` has SIX (the `analysis`
  page, #541); entries are built dynamically from `.proto-nav-btn[data-page]` instead.
- **[UNVERIFIED] resolved during intake:** reset button binding is at
  `app.js:857-864` (not :727-735); `.modal-*` styles at `styles.css:731-775` (not
  :667-676); `#reset-modal` markup at `index.html:839-848` (not :697);
  `orderGroupMarket` takes 4 args (`app.js:6202-6210`); `#ot-search-input` has an
  `input` listener (`:8108-8124`) AND a render-time re-filter (`:8027-8033`).

## Dependency graph

T1 (shell + keyboard + a11y) → T2 (entries + filter + arrows) → T3 (market search)
→ Verification (focused suites + tsc parity + operator How-to-verify)

## Locked interfaces (frozen for this branch)

- New file `dashboard/static/command-palette.js` — IIFE, browser guard shaped like
  `prototype.js:511-516`, testable exports on `module.exports`. Frozen exports:
  - `initPalette(doc)` — idempotent; no-op, no throw when `#command-palette` is absent.
  - `openPalette()` / `closePalette({ restoreFocus = true })` — `restoreFocus: false`
    is the RESET entry's close (the typed-confirm modal takes focus next).
  - `isPaletteOpen()`
  - `buildEntries(doc)` → `{ id, label, group, sub?, disabled?, run }[]` — pages
    dynamically from `.proto-nav-btn[data-page]` (NEVER hardcoded — six pages today);
    actions: SYNC VENUE (`#btn-sync`), RESET (sub-label "Opens the typed
    confirmation.").
  - `filterEntries(entries, query)` — trimmed, case-insensitive substring on label.
  - `marketEntries(state, kpi, query)` — sources `kpi.by_market`,
    `state.orders[].condition_id`, `state.pairs[].market`; dedupe by condition id;
    label `title || name || slug` else first 10 chars of the id (mirrors
    `orderGroupMarket`, `app.js:6202-6210`, so palette labels match the tables —
    mirrored inline as one code path, no `typeof` guard); ≥2 chars; cap 8 under a
    "Markets" heading; neither snapshot → inert "No markets loaded yet." row.
  - `moveHighlight(delta)` / `activateHighlighted()` — arrows bounded at both ends.
- DOM contract (frozen): `#command-palette` overlay = `.modal-overlay` + `hidden`,
  `role="dialog"`, `aria-modal="true"`, `aria-labelledby` → visible `.modal-title`
  "Command palette"; `input#palette-search` with `role="combobox"`, `aria-controls`,
  `aria-expanded`, `aria-activedescendant`; `#palette-list` `role="listbox"`; rows
  `role="option"`, `aria-selected="true"` on the highlighted row only; hint line
  "Use arrow keys to move. Press Enter to choose. Press Escape to close."; overlay
  beside `#reset-modal` in `index.html`; script tag after `prototype.js`, no
  `async`/`defer`; palette CSS reuses `.modal-*` unchanged, new `palette-` rules keep
  `z-index:1000`.
- Key contract (frozen): ONE `window` keydown listener, capture phase. Cmd/Ctrl+K
  (case-insensitive, `preventDefault`) opens from anywhere incl. inputs, and while
  open selects the input text; `/` opens only with no modifier, outside
  INPUT/TEXTAREA/SELECT/contenteditable, and when not `defaultPrevented`; never opens
  while `#reset-modal` has `.show`; while open ArrowUp/Down/Enter/Escape/Tab each
  `preventDefault()` + `stopPropagation()`; focus trap via `focusin` guard (the
  input is the only tabbable control); backdrop click closes only when the target is
  the overlay itself.

## Tasks

### T1 [L] [UX/UI] Palette shell — markup, styles, open/close, keyboard ownership
- Files: `dashboard/static/index.html`, `dashboard/static/styles.css`,
  `dashboard/static/command-palette.js` (NEW),
  `tests/js/command_palette_harness.cjs` (NEW),
  `tests/test_dashboard_command_palette.py` (NEW)
- Build: overlay markup beside `#reset-modal` (pattern at `index.html:839-848`);
  script tag after `prototype.js`; `palette-` CSS; the IIFE per the frozen contract;
  harness per `tests/js/prototype_nav_harness.cjs` (scenario JSON on argv[2], stub
  DOM, `new Function('module','exports','document','window', source)` evaluation)
  extended with: capture-before-bubble dispatch, key events with observable
  `defaultPrevented` and `stopPropagation` that ends dispatch, `focus()` updating
  `activeElement`, `fetch` spy + `EventSource` stub, document-level kanban/reset
  Escape spies; pytest wrapper with the `shutil.which("node")` skip pattern from
  `tests/test_dashboard_input_lag.py:31-32`.
- Verify (RED first): markup is a hidden labelled dialog; script loads after
  `prototype.js`; `/static/command-palette.js` served 200 (dashboard test client) and
  `node --check` clean; init safe with no markup; Ctrl+K/Meta+K (+ uppercase K) open
  and focus the search; Escape closes and restores focus (body when the opener is
  gone); `/` opens only outside editables and without modifiers; ignored while
  `#reset-modal.show`; backdrop vs inner click; Tab/Shift+Tab hold focus; background
  spies get zero calls while open; closed palette has zero `role="option"` rows.
- Depends on: nothing.

### T2 [M] [UX/UI] Entries — pages, actions, filtering, arrow navigation
- Files: `dashboard/static/command-palette.js`,
  `tests/js/command_palette_harness.cjs`,
  `tests/test_dashboard_command_palette.py`
- Build: `buildEntries` per contract — pages from `.proto-nav-btn[data-page]`
  (external rail links carry the class WITHOUT `data-page` and must be excluded),
  SYNC VENUE (`#btn-sync`, `aria-disabled` + no-op when disabled), RESET (close
  without focus restore → click `#btn-reset`; never `fetch`); input-event filtering;
  arrow highlight bounded + scrolled into view; Enter/click activation; "No
  matches." row; harness evaluates the REAL `prototype.js` first (nav fixture shape
  from `prototype_nav_harness.cjs`) plus one external `.proto-nav-btn` link,
  `#btn-sync`, `#btn-reset` click spies.
- Verify (RED first): entries = six pages + SYNC VENUE + RESET, external link
  absent; "rep"/"REP" filter leaves Reports & Analytics + RESET; arrows track one
  `aria-selected`/`aria-activedescendant` and stop at both ends; no-match Enter does
  nothing; choosing Reports & Analytics leaves only `page-reports` visible with
  `aria-current="page"` and `localStorage["sh-proto-page"] = "reports"`; SYNC clicks
  once (zero when disabled); RESET opens the confirm with zero `fetch` and no focus
  restore.
- Depends on: T1.

### T3 [x] [M] [UX/UI] Market search from the loaded state
- Files: `dashboard/static/command-palette.js`,
  `tests/js/command_palette_harness.cjs`,
  `tests/test_dashboard_command_palette.py`
- Build: `marketEntries` per contract (inline label mirror of `orderGroupMarket`);
  rows computed only on `input` — polls/snapshot changes never rebuild an open list;
  selection **[PROPOSED — operator opt-in pending]**: click `home`, set
  `#ot-search-input`, dispatch `input`, focus it; harness fixtures: `lastKpi.by_market`
  + `lastState` with one market in both sources and one untitled, plus an
  `#ot-search-input` spy.
- Verify (RED first): title match case-insensitive; full condition-id match; two-source
  dedupe → one row; two-char minimum + eight-row cap; neither snapshot → inert
  "No markets loaded yet."; selection navigates home with the label in the input, one
  `input` event, input focused (untitled → ten-char prefix); snapshot change mid-open
  leaves rows, query, and highlight untouched.
- Depends on: T2.

## Checkpoints

- After T1: the palette opens/closes, owns the keyboard, traps and restores focus —
  all a11y contracts proven offline.
- After T2: the feature is end-to-end usable (pages, SYNC, RESET, filtering) — this
  slice alone is shippable.
- After T3: market search complete; run Verification.

## Verification (agent-run only — never handed to the operator)

- Node present (verified: hermes toolchain node 26.7.0). New file alone first, cwd =
  worktree, zero skips expected:
  `"C:/Users/Tiger/Agents/Projects/AI Trading/spread-hunter/.venv/Scripts/python.exe" -m pytest tests/test_dashboard_command_palette.py -q`
- The issue's selection, unchanged (covers `test_dashboard_server.py`,
  `test_dashboard_input_lag.py`, `test_live_state_language.py`,
  `test_analytics_surface_mount.py`, `test_sidebar_prototype.py` incl. the
  script-order assertion at `:348`):
  `"C:/Users/Tiger/Agents/Projects/AI Trading/spread-hunter/.venv/Scripts/python.exe" -m pytest tests/ -k "live_state or input_lag or analytics_surface or start_toggle or dashboard" -q`
- PORT note: this host's shell sometimes exports `PORT=0`, which breaks dashboard
  collection — prefix `unset PORT;` when set. Planning session verified clean.
- tsc parity (verified at intake, cwd = worktree):
  `npx -y -p typescript@5.7.3 tsc --noEmit server.ts` fails on `main` with exactly one
  pre-existing error — `server.ts(251,111): error TS1343` (`import.meta` with the
  default module setting; no `tsconfig.json`, `package.json` is `"type": "module"`).
  Acceptable result after the change: the SAME single error, never fewer, never more.
  Never edit `server.ts`.
- Operator How-to-verify (goes in the PR, hands-on only per
  `docs/agents/verifying.md`): press Ctrl+K → "Command palette" dialog with the cursor
  in the search box; type "reports" → Enter → only Reports & Analytics visible, and it
  survives a reload; press `/`, Tab a few times, Escape → focus stays in the search
  then returns to where it was; choose RESET → Cancel → typed-confirm appears, nothing
  reset; type part of a market title → Enter → Dashboard page with Orders & Trades
  filtered. Local bridge, `BRIDGE_CONTROL` unset; no START, no SYNC against
  production, no confirmed RESET — zero dollar risk. PowerShell `;` between commands.

## Resolved open questions (from the issue + CodeRabbit, answered from code)

1. "Three pages" vs the issue's own five — live `PAGE_LAYOUT` (`prototype.js:38-84`)
   has SIX (`analysis`, #541). Entries are dynamic; no hardcoded page list anywhere.
2. "The same switch function" is private — the palette clicks the real sidebar button
   (persistence, `aria-current`, drawer close all run).
3. Background key handlers — TWO document-level listeners exist (reset Escape
   `app.js:776-780`, kanban arrows `app.js:9120-9128`); the capture-phase listener
   silences both while open.
4. Choosing a market result is unspecified by the issue — see the opt-in question put
   to the operator.

## Rejected proposals (recorded so they do not resurface)

- by_market-only market search (dropped at intake): `orderGroupMarket`'s pairs
  fallback exists precisely for order groups the KPI did not break down — one source
  would hide markets the tables render. Three sources stay.
- Sub-issue fan-out for T1-T3 (skipped): single cohesive frontend feature; the repo's
  child-ticket convention (`docs/agents/issue-tracker.md:81-83`) targets map-style
  multi-workstream decomposition. `tasks/plan.md` is the durable mirror.

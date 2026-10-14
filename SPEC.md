# SPEC — #477: Command palette for pages, actions, and market search

Branch: `spread-hunter-i477` | Tier: Standard | Type: UX/UI (keyboard-first dashboard feature)

Supersedes the #531 spec in this file's history (kept in git).

## Goals

1. Cmd/Ctrl+K anywhere on the dashboard opens a command palette; `/` is an alias;
   Escape and backdrop click close it; focus returns to the element focused before
   opening.
2. Typing filters the entry list (trimmed, case-insensitive substring);
   ArrowUp/ArrowDown move the highlight (bounded); Enter activates.
3. A page entry navigates exactly like clicking the sidebar item — page visibility,
   `aria-current`, `sh-proto-page` persistence, drawer close — and covers every page
   the rail has (six today), built dynamically, never hardcoded.
4. SYNC VENUE triggers the existing `#btn-sync` click; RESET opens the existing
   typed-confirm modal and executes nothing.
5. Market search finds markets by title or full condition id from the currently
   loaded state (`lastKpi.by_market`, `lastState.orders`, `lastState.pairs`),
   deduped, with the same labels the tables show.
6. Accessibility is part of the feature, not a follow-up: `role="dialog"`,
   `aria-modal="true"`, a visible title, a focus trap while open, and the combobox
   pattern (focus stays in the input; `aria-activedescendant` tracks the highlight).
7. Closed, the palette is not focusable or tabbable and leaks no DOM.

## Out of scope (from the issue — binding)

- No new dependency, no framework migration — this dashboard is vanilla HTML/CSS/JS.
- No new pages, no data endpoints — the palette only surfaces what the page already
  renders and the actions the existing buttons already perform.
- No changes to `server.ts` or the Python API.
- No edits to `app.js`, `prototype.js`, or any existing test file.

## Resolved open questions (defaults adopted, confirmed against code)

1. **Six pages, not five.** The issue's parenthetical lists five; live `PAGE_LAYOUT`
   (`prototype.js:38-84`) has six — `analysis` ("Data Analysis") arrived with #541.
   Entries come from the DOM (`.proto-nav-btn[data-page]`), so the palette tracks the
   rail automatically.
2. **Click the button, don't call the switch.** `showPage` is private to the
   `prototype.js` IIFE in the browser; a real sidebar click runs the full behavior.
3. **Two background listeners to silence.** Document-level reset-Escape
   (`app.js:776-780`) and kanban ArrowLeft/ArrowRight (`app.js:9120-9128`); the
   palette's capture-phase listener with `stopPropagation` keeps both silent while
   open.
4. **Choosing a market result** — the issue specifies search, not the result action.
   Opt-in question put to the operator; the recommended default (prefill the existing
   Orders & Trades filter on the Dashboard page) is marked `[PROPOSED]` in
   `tasks/plan.md` and enters the build only on approval.

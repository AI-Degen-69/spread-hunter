# Plan — #586: Unify top-nav pill grammar across header, services dropdown, and DB switcher

Branch: i586/unify-top-nav-pill-grammar-across-header | Issue: #586

## Why (issue summary)

Follow-up to #504 (shipped as #575). The operator reviewed the live header and found four inconsistencies: (1) amber means both execution-state and registry-mode at once (`SHADOW STOPPED` vs `DB SHADOW`); (2) the DB run-switcher uses unrelated `.db-run-row` markup instead of the pill grammar; (3) the dropdown meta slot mixes `N alerts` with full sentences (`live only (shadow uses in-process client)`), which wrap and squeeze names into ellipsis; (4) the dropdown row builder emits the liveness dot with no separator, so the ENGINE dot touches its letter.

## Tier: Small (3 code files + 1 new test file, no logic changes, CSS + markup refactor)

## Type: Design/UI + Code

- [Design/UI + Code] T1 — Dropdown rows: renderPillHtml pills, fixed meta vocabulary, tooltip move.
- [Design/UI] T2 — Mode-tint refactor + dropdown grid + meta ellipsis.
- [Design/UI + Code] T3 — DB switcher rows + demo row adopt pill grammar.
- [Code] T4 — New consistency suite + full regression.

## CodeRabbit plan intake (2026-10-15)

- No plan comment requested at planning time (follow-up scope is operator-specified from the live screenshot; Station III must not wait for it).

## Resolved questions (from issue, code & operator)

- **DB value wording**: keeps `SHADOW`/`LIVE`; disambiguation via shell tint + filename detail + tooltip (SPEC Q1).
- **Switcher FINISHED/ENDED**: kept, reuse stopped gray tone (SPEC Q2).
- **Demo row**: adopts pill grammar (`DEMO` + `LOADED`/`AVAILABLE`) (SPEC Q3).
- **Scope breadth**: operator chose Full unification (all three surfaces).

## Improvement proposal (evidence-based, adopted by default)

- **Reuse `renderPillHtml` for dropdown row pills instead of hand-rolled dot+label**:
  - *Evidence*: `app.js:1814-1817` builds `` `${dot}<span class="pill-label">` `` — dot with no separator, and the pill carries the *state* as its label, so every row reads DOWN/DOWN with no service name in the pill. `renderPillHtml` (`app.js:292-322`) already emits dot + label + value + detail with proper spacing.
  - *Improvement*: row pills become `renderPillHtml({label: <service short tag>, value: <state>, dotState: state})` — e.g. label `ENGINE` + value `DOWN`. One helper, one spacing, dot fixed for free.
  - *Classification*: simplification → adopted by default.

## Dependency graph

```
T1 (Dropdown row pills + meta vocab)
├── T2 (Mode tint + grid CSS)
└── T3 (Switcher pill rows)
    └── T4 (Consistency suite + regression)
```
## Tasks

### T1 [ ] [Design/UI + Code] Dropdown rows: pill-helper pills, service labels, fixed meta vocabulary, tooltip move — M
- **Target files**: `dashboard/static/app.js`, `dashboard/static/index.html`.
- **What**:
  - In `renderServicesDropdown` (`app.js:1766-1821`), replace the hand-rolled `${dot}<span class="pill-label">` pill with `renderPillHtml({label: <short tag>, value: <STATE>, detail: <meta>, dotState: state})`. Short tags: `WATCHDOG`, `SCAN`, `ENGINE`, `SHADOW`.
  - Fix the meta vocabulary: `pid <n> · <uptime>`, `N alerts`, `auto-watch`, `telemetry down`, `stopped`, `rehearsal ended`, heartbeat age. Move `live only (shadow uses in-process client)` / `shadow only (live uses venue engine)` from the meta slot to the row's `title` attribute.
  - Guardrail branch keeps alerts logic; only pill construction and meta strings change.
  - Update the four fallback rows in `index.html:101-120` to the same label+value span shape.
- **Verification**: `python -m pytest tests/test_ui_top_nav_groups.py -q` (existing pins hold).
### T2 [ ] [Design/UI] Mode-tint refactor + dropdown grid + meta ellipsis — S
- **Target files**: `dashboard/static/styles.css`.
- **What**:
  - Refactor `.pill.mode-live` / `.pill.mode-shadow` so background/border carry mode (emerald vs amber wash) while `.pill-value` text color comes from `state-*` only. `mode-shadow.state-stopped` value text becomes stopped gray, not `#f59e0b`; amber survives on the shell. Same for `mode-live.state-stopped`.
  - Convert `.services-dropdown-row` to grid: `grid-template-columns: 150px 1fr 140px`; `.services-dropdown-meta` gets `max-width: 140px; text-align: right; ellipsis`.
  - Widen `.services-dropdown` 340px → 380px. Add `.services-dropdown-row .pill` min-width 0 + ellipsis guard.
- **Verification**: `python -m pytest tests/test_ui_top_nav_groups.py tests/test_ui_277_touch_targets.py -q` + visual check on demo.
### T3 [ ] [Design/UI + Code] DB switcher rows + demo row adopt pill grammar — M
- **Target files**: `dashboard/static/app.js`, `dashboard/static/styles.css`.
- **What**:
  - In `renderRunSwitcher` (`app.js:1603-1619`), replace `.db-run-name` + `.db-run-state` spans with a single `.pill` built by `renderPillHtml({label: <run short id>, value: <RUNNING/FINISHED/ENDED>, detail: <age>})`; keep port link and SWITCH/THIS PAGE controls untouched. Pill carries `state-running`/`state-stopped` for text color.
  - `demoSwitcherRow` (`app.js:1529-1541`): same treatment — `DEMO` label + `LOADED`/`AVAILABLE` value pill beside the toggle button.
  - CSS: `.db-run-row` becomes the same grid as dropdown rows; port link and switch button styles preserved.
- **Verification**: `python -m pytest tests/test_ui_top_nav_groups.py tests/test_dashboard_server.py -q`.
### T4 [ ] [Code] New consistency suite + full regression — S
- **Target files**: `tests/test_ui_top_nav_consistency.py` (new).
- **What**:
  - Pin label/value/detail roles: every header pill, dropdown row pill, and switcher pill contains `.pill-label` (gray, uppercase) + `.pill-value` (state-colored), never a bare state-only pill.
  - Pin meta vocabulary: detail slots match the allow-list (`pid …`, `N alerts`, `auto-watch`, `telemetry down`, `stopped`, `rehearsal ended`, filename, age); live-only/shadow-only sentences appear in `title` attributes, never in visible meta.
  - Pin grid columns + ellipsis rules in CSS; pin dot separation (no `><span class="pill-label"` without gap in `renderServicesDropdown` source); pin tooltip relocation.
  - Full run: new suite + `test_ui_top_nav_groups` + `test_ui_278_status_strip` + `test_ui_277_touch_targets` + `test_sidebar_prototype` + `tests/ -k "live_state or dashboard"`.
- **Verification**: the command above, all green.

# Plan — #504: Reorganize the top nav bar with consistent grouping and clear hierarchy

Branch: i504/improve-reorganize-the-top-nav-bar-with-consistent | Issue: #504

## Why (issue summary)

The top status strip (`.top-meta`) mixes seven status pills, exposure bar, and three buttons in an unstructured row with no grouping. `#services-dropdown` is nested inside `.services-pill-host` causing scroll clipping on desktop. Status pills lack a consistent HTML grammar, run buttons duplicate geometry without a shared base class, and kanban bucket heroes dilute the live-state color vocabulary with five categorical hues. Consolidated #479 collapses individual service pills into a single worst-state aggregate pill with a detail popover and neutralizes hero card hues.

## Tier: Standard (5 code files + 4 test files, cohesive UI architecture overhaul)

## Type: Design/UI + Code + UX/Copy

- [Design/UI] T1 — Semantic groups, accessibility landmarks & tab-row styling alignment.
- [Code] T2 — Status pill grammar normalization, escaped helper & fake DOM harness updates.
- [Design/UI] T3 — Shared button hierarchy (`.ctrl-btn`), touch targets & master-toggle handler.
- [Design/UI] T4 — Panel anchoring, unified placement (`placePanel`) & click/escape dismissal.
- [Design/UI] T5 — Live state aggregation & neutral kanban hero palette (Consolidated #479).

## CodeRabbit plan intake (2026-10-14)

- **Adopted**:
  - Semantic child groups inside `.top-meta` (`Live state`, `Data source`, `Run controls`) removing mixed outer group role/aria-label while keeping `tabindex="0"` for keyboard scrolling (#278).
  - Normalizing status pills to `.pill mono` base with `.pill-label`, space, and `.pill-value` spans.
  - Adding `textContent` derivation to fake DOMs (`live_state_harness.cjs`, `master_toggle_harness.cjs`, `start_toggle_harness.cjs`) by stripping tags and decoding basic entities.
  - Shared `.ctrl-btn` base with `.ctrl-btn-primary` and `.ctrl-btn-secondary` variants; updating exact class equality in tests to token checks.
  - Moving `#services-dropdown` to direct child of `<header>` and unifying placement logic.
- **Rejected/Modified**:
  - CodeRabbit initially flagged #479 as independent due to 401 credential error; #479 was actually closed as consolidated into #504 by the issue owner. Scope includes T5 for aggregate pill + neutral kanban hero recolor.
  - Maintained `#btn-command-palette` (added in #563) inside `.top-group-controls`.

## Resolved questions (from issue, code & operator)

- **Degraded services count**: Aggregate pill displays worst single state only (DOWN > DEGRADED > RUNNING); individual counts and breakdown live in the detail popover.
- **Kanban bucket icons**: Keep existing text labels, remove categorical colors, no new icon set.
- **Search button placement**: `#btn-command-palette` remains adjacent to run controls inside `.top-group-controls`.

## Improvement proposal (evidence-based, adopted by default)

- **Unified Header Panel Geometry & Auto-Dismissal**:
  - *Evidence*: `dashboard/static/index.html:78-81` nests `#services-dropdown` in `.services-pill-host`, while `#db-run-switcher` sits as a direct child of `<header>` (`index.html:111`).
  - *Improvement*: Both panels share `.header-panel` base class and a single `placePanel(trigger, panel)` helper with viewport left-clamping (margin >= 4px), dynamic repositioning on strip scroll and resize, and mutual exclusivity (opening one closes the other; Escape closes both).

## Dependency graph

```
T1 (Groups & Landmarks)
├── T2 (Pill Grammar)
│   └── T5 (State Aggregation & Kanban Colors [Consolidated #479])
├── T3 (Button Hierarchy)
└── T4 (Panel Anchoring & Dismissal)
```

- T1 has no dependencies (establishes DOM group structure).
- T2 and T3 depend on T1 (styles and markup operate within groups).
- T4 depends on T1 and T2 (anchors to normalized service and DB triggers).
- T5 depends on T2 and T4 (leverages pill grammar and panel popover).

---

## Tasks

### T1 [Design/UI] Semantic Groups, Accessibility Landmarks & Tab Row Alignment — M
- **Target files**: `dashboard/static/index.html`, `dashboard/static/styles.css`, `tests/test_ui_top_nav_groups.py`, `tests/test_dashboard_server.py`.
- **What**:
  - Keep `.brand` as first header child.
  - Inside `.top-meta`, introduce 3 direct child groups:
    - `.top-group.top-group-state` with `role="group"` and `aria-label="Live state"`.
    - `.top-group.top-group-source` with `role="group"` and `aria-label="Data source"`.
    - `.top-group.top-group-controls` with `role="group"` and `aria-label="Run controls"`.
  - Remove mixed `role="group"` and `aria-label` from `.top-meta`, preserving `tabindex="0"`.
  - Add inline-flex `.top-group` rules with 6px item gap, group dividers, and responsive wrapping for `<=900px`.
  - Align tab-row styling with header (radius, padding, uppercase font, active tint).
  - Create `tests/test_ui_top_nav_groups.py` with element tree assertions and update `test_dashboard_server.py` group check.
- **Verification**: `python -m pytest tests/test_ui_top_nav_groups.py tests/test_ui_278_status_strip.py tests/test_sidebar_prototype.py -q`.

### T2 [Code] Status Pill Grammar Normalization & Fake DOM Harnesses — M
- **Target files**: `dashboard/static/index.html`, `dashboard/static/styles.css`, `dashboard/static/app.js`, `tests/js/live_state_harness.cjs`, `tests/js/master_toggle_harness.cjs`, `tests/js/start_toggle_harness.cjs`, `tests/test_live_state_language.py`, `tests/test_ui_top_nav_groups.py`.
- **What**:
  - Normalize pill markup in `index.html` to `.pill mono` base with `.pill-label` and `.pill-value` spans.
  - Add `.pill-value` style in `styles.css`. Reduce `#scan-engine-elsewhere` to color modifier only.
  - Add `renderPillHtml(label, value, detail, options)` helper in `app.js`.
  - Update `renderMarketScanPill`, `renderServiceHeader`, master indicator branches, `renderDbMode`, and elsewhere renderers to use helper.
  - Update Node fake DOM harnesses to strip HTML tags and decode entities (`&amp;`, `&lt;`, `&gt;`, `&quot;`, `&#39;`) on `innerHTML` setter.
  - Update elsewhere assertion in `tests/test_live_state_language.py`.
- **Verification**: `python -m pytest tests/test_ui_top_nav_groups.py tests/test_live_state_language.py -q`.

### T3 [Design/UI] Unified Button Hierarchy & Master Toggle State Handler — S
- **Target files**: `dashboard/static/index.html`, `dashboard/static/styles.css`, `dashboard/static/app.js`, `tests/test_dashboard_server.py`, `tests/test_ui_top_nav_groups.py`, `tests/test_ui_277_touch_targets.py`.
- **What**:
  - Add `.ctrl-btn` base class, `.ctrl-btn-primary` (weight 800, 16px pad), and `.ctrl-btn-secondary` (weight 700, 14px pad).
  - Strip redundant geometry from existing button rules while preserving colors and hover states.
  - Update markup in `index.html` for `#btn-master-toggle`, `#btn-sync`, `#btn-reset`, and `#btn-command-palette`.
  - Add helper in `app.js` for master toggle class assignment (`getMasterToggleClasses`).
  - Update `tests/test_dashboard_server.py` to use token checks instead of exact string equality.
- **Verification**: `python -m pytest tests/test_ui_top_nav_groups.py tests/test_ui_277_touch_targets.py -q`.

### T4 [Design/UI] Panel Anchoring, Unified Positioning & Dismissal — M
- **Target files**: `dashboard/static/index.html`, `dashboard/static/styles.css`, `dashboard/static/app.js`, `tests/test_ui_top_nav_groups.py`, `tests/test_live_state_language.py`.
- **What**:
  - Move `#services-dropdown` to direct child of `<header>` alongside `#db-run-switcher`. Add `.header-panel` class to both.
  - Add `.header-panel` shared absolute styling (safe top default, `z-index: 60`, max-width clamp).
  - Extract `placePanel(trigger, panel)` helper in `app.js` with left-edge margin clamp (>=4px).
  - Reposition panels on strip scroll and window resize.
  - Implement outside click and Escape key dismissal; opening one panel closes the other.
- **Verification**: `python -m pytest tests/test_ui_top_nav_groups.py tests/test_live_state_language.py -q`.

### T5 [Design/UI] Live State Aggregation & Neutral Kanban Hero Palette (Consolidated #479) — M
- **Target files**: `dashboard/static/index.html`, `dashboard/static/styles.css`, `dashboard/static/app.js`, `tests/test_ui_top_nav_groups.py`.
- **What**:
  - Implement summary aggregate status pill in live state group computing worst-state-wins (`DOWN` > `DEGRADED` > `RUNNING`) via `stateKey`.
  - Clicking aggregate pill opens services popover detailing individual service states and heartbeat age.
  - Neutralize kanban bucket hero styling in `styles.css`: remove categorical hues (`#a78bfa`, `#fb923c`, `#f472b6`, `#38bdf8`, `#fbbf24`), replacing with neutral slate/ink chip styling.
  - Confirm green/amber/red/gray are strictly reserved for live execution status.
- **Verification**: `python -m pytest tests/test_ui_top_nav_groups.py tests/test_dashboard_server.py -q` and `npm run build`.

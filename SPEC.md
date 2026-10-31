# SPEC.md — Issue #867: Redesign Dashboard Layout and Theme

Branch: `i867/redesign-dashboard-layout-and-theme` | Issue: #867

## 1. Goal

Redesign the dashboard header controls, dropdown popovers, kanban lane switcher, and typography so interactive controls look and behave like controls (buttons with downward carets `▾` and connected segmented tabs), passive status badges keep clean pill styling with WCAG AA contrast, redundant port numbers are removed from tournament arm labels, and 11–13px text renders crisply across Windows and Linux.

## 2. Non-Goals (Out of Scope)

- Backend API changes (`dashboard/server.py`, `core_brain/**`, `scripts/*.py`).
- Data polling, SSE socket logic, or start/stop modal orchestration in `dashboard/static/app.js`.
- Introducing new pages, panels, or layout routing (`dashboard/static/prototype.js`).
- Rewriting or modifying `data/orders.db` or `data/demo_orders.db`.

## 3. Components & Behaviour

### 3.1 Typography & Status Pill Legibility (`dashboard/static/styles.css`)
- **Font stacks with OS-native fallbacks:** Extend `body`, `.mono`, and `--font-display` font-family declarations with `system-ui, 'Segoe UI'` before the generic family (`sans-serif` / `monospace`). Retain literal `'IBM Plex Sans'` in `.tab-btn` for existing test compatibility.
- **Font weights & minimum sizes:**
  - Set `body` base `font-weight: 500` so body copy renders crisply on Windows subpixel antialiasing.
  - Raise `.pill-label` and `.pill-detail` `font-size` from `9px` to `11px`.
  - Brighten `.pill.state-unknown` text color to `#cbd5e1` (or `var(--text-secondary)` `#94a3b8` with dark border) so contrast on the slate background meets ≥ 4.5:1 while remaining distinct from `.state-stopped`.

### 3.2 Top Nav Dropdown Triggers (`dashboard/static/index.html`, `styles.css`, `app.js`)
- **Semantic button for DB trigger:** Change `#db-mode-badge` in `index.html` from `<span>` to `<button type="button">` with `aria-haspopup="true"`, `aria-expanded="false"`, and `aria-controls="db-run-switcher"`. Preserve its `id` and `.pill state-unknown mono` classes.
- **Unified dropdown button affordance (`.top-meta .pill[aria-haspopup]`):**
  - Style `#profile-badge`, `#db-mode-badge`, and `#hud-services-pill` via `.top-meta .pill[aria-haspopup]` with a subtle button container (`border-radius: 6px`, dark surface fill, crisp border, `cursor: pointer`, `font-size: 12px`, `font-weight: 600`).
  - Add an explicit downward caret via `.top-meta .pill[aria-haspopup]::after { content: "▾"; ... }` that rotates `180deg` when `[aria-expanded="true"]`, and remove the duplicate literal `.services-caret` span from `#hud-services-pill` so every dropdown trigger renders a single unified caret.
  - Truncate lengthy DB store filenames gracefully inside `#db-mode-badge .pill-detail` (`max-width: 180px`, `overflow: hidden`, `text-overflow: ellipsis`, `white-space: nowrap`) so long filenames (`02_shadow_30-10_09-15_loose-baseline.db`) never bloat the navbar.
- **Resilient DB switcher wiring (`renderDbMode` & `closeRunSwitcher`):**
  - Wire the click/outside-click/Escape listeners on `#db-mode-badge` *before* the `if (!mode)` early return in `renderDbMode()`, matching `renderProfileBadge()`.
  - Synchronize `aria-expanded="true"` / `"false"` on `#db-mode-badge` whenever `#db-run-switcher` opens or closes (including `closeRunSwitcher()`, outside click, and Escape).

### 3.3 DB Run Switcher & Profile Popover Polish (`dashboard/static/app.js`, `styles.css`)
- **Port-free tournament arm labels:** In `runSwitcherLabel(run)`, omit `portPart` (`:${run.dash_port}`) so tournament arms render `#01 aggressive (pid 4242)` instead of `#01 aggressive:8801 (pid 4242)`.
- **Unambiguous active & staged state in Profile Popover (`profileOptionsHtml`, `armRowHtml`, `presetRowHtml`):**
  - Pass optional `activeProfile` (`lastStatus?.active_profile || null`) into `profileOptionsHtml(options, pending, activeProfile)`.
  - Match the active profile against an arm (`activeProfile.name === arm.name || activeProfile.tournament_arm === arm.name`) or preset (`!activeProfile.tournament_arm && (activeProfile.name === preset.name || activeProfile.preset === preset.name)`).
  - Mark the matching row with `.active` class and `<span class="arm-active-tag">✓ ACTIVE</span>` alongside existing `.staged` / `STAGED` support, styled with a distinct cyan/blue highlight so the currently loaded configuration is unmistakable at a glance.
- **DB Run Switcher row layout (`.db-run-row`):**
  - Widen `.db-run-switcher` (`width: 420px`) and refine `.db-run-row` (`grid-template-columns: minmax(0, 1fr) auto`) so run names in `.pill-label` never truncate abruptly at 150px (`..._loo`) while keeping `.pill`, `.pill-label`, `.pill-value`, and `.pill-detail` intact.

### 3.4 Connected Kanban Lane Switcher & Prototype Alignment (`styles.css`, `prototype.css`, `app.js`)
- **Connected segmented control (`.kanban-lane-switcher`):**
  - Remove button gaps (`gap: 0`, `padding: 0`, `overflow: hidden`, unified border and `border-radius: 8px`).
  - Style `.lane-tab-btn` segments with `border-radius: 0`, `border: none`, and `border-right: 1px solid var(--border-subtle)` between segments (`:last-child { border-right: none }`), keeping distinct active tints for `regular` (cyan), `live` (purple), and `all` (neutral) and raising `.lane-count-pill` from `10px` to `11px`.
  - At `@media (max-width: 900px)`, give `.lane-tab-btn` `flex: 1 1 0`, `min-width: 0`, and truncate `.lane-name` with `text-overflow: ellipsis` while keeping `.lane-count-pill` visible.
  - In `window.setKanbanLane`, write `aria-pressed` (`'true'` / `'false'`) to match `index.html`.
- **Prototype stylesheet alignment (`dashboard/static/prototype.css`):**
  - Edit existing rules in place (no duplicate selectors): raise `.proto-sub-nav-btn` weight to `600` (and `[aria-current="subpage"]` to `700`), `.proto-body .ot-tab-btn` to `12px` / `600`, `.proto-body .ot-tab-btn .ot-count` to `11px`, `.proto-body .stitch-category-pill` weight to `600`, and `.proto-body .pill.active` / `.proto-body .pill.reconnecting` to `11px`.

## 4. Acceptance Criteria

- [ ] `PROFILE`, `DB`, and `SERVICES` render as `<button type="button">` controls with `aria-haspopup="true"`, `aria-expanded`, `aria-controls`, button border-radius (`6px`), and a single downward caret (`▾`).
- [ ] `#db-mode-badge` truncates lengthy filenames gracefully with `text-overflow: ellipsis` and opens `#db-run-switcher` even when initial status mode is unknown.
- [ ] `runSwitcherLabel()` omits `:88xx` port numbers from tournament arm labels while preserving arm index, name, and PID suffix.
- [ ] `#profile-dropdown` marks the currently active profile/preset with `.active` and `✓ ACTIVE` in addition to `STAGED`.
- [ ] `.kanban-lane-switcher` is styled as a zero-gap connected segmented control and `setKanbanLane()` updates `aria-pressed`.
- [ ] `body`, `.mono`, and `--font-display` include `system-ui, 'Segoe UI'` fallbacks, `body` weight is `500`, `.pill-label`/`.pill-detail`/`.lane-count-pill` are `11px`, and `prototype.css` weights/sizes match.

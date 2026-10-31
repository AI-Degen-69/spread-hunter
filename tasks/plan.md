# Implementation Plan — Issue #867: Redesign dashboard layout and theme (pill misuse, font legibility, redundant port numbers)

**Issue:** [#867](https://github.com/AI-Degen-69/spread-hunter/issues/867)
**Branch:** `i867/redesign-dashboard-layout-and-theme`
**Worktree:** `C:\Users\Tiger\.worktrees\spread-hunter\i867`
**Spec:** [`SPEC.md`](../SPEC.md)
**Constraints:** [`CONSTRAINTS.md`](../CONSTRAINTS.md)

## Overview

Fix four dashboard UI/UX pain points identified in Issue #867:
1. **Interactive dropdown triggers (`PROFILE`, `DB`, `SERVICES`) disguised as static pills:** Promote `#db-mode-badge` to `<button type="button">` with `aria-haspopup="true"`, `aria-expanded="false"`, and `aria-controls="db-run-switcher"`, style `.top-meta .pill[aria-haspopup]` as a button with a `6px` border-radius and a single rotating `▾` caret (`::after`), remove the duplicate literal `.services-caret`, add graceful `.pill-detail` ellipsis truncation on `#db-mode-badge`, and wire `#db-mode-badge` before `if (!mode)` in `renderDbMode()` so clicking works even when the initial status mode is unknown.
2. **DB run switcher premature truncation (`..._loo`), redundant port numbers (`:8800`), and missing active profile indicator:** Remove `:port` from `runSwitcherLabel()`, widen `.db-run-switcher` and update `.db-run-row` from `150px 1fr auto` to `minmax(0, 1fr) auto` (while preserving `auto 1fr auto` on `.db-demo-row`) so run rows use full popover width without breaking `.pill` / `.pill-label` / `.pill-value` grammar, and add an unmistakable `✓ ACTIVE` badge + `.active` highlight to the currently active profile/preset row in `#profile-dropdown`.
3. **Disconnected kanban lane switcher & fragile Windows typography:** Restyle `.kanban-lane-switcher` as a unified connected segmented control (`gap: 0`, shared outer border, internal dividers, responsive narrow-viewport flex) with `aria-pressed` toggled by `window.setKanbanLane()`, add `system-ui, 'Segoe UI'` fallbacks to `body`, `.mono`, and `--font-display`, raise `body` font-weight to `500`, raise `.pill-label`, `.pill-detail`, and `.lane-count-pill` to `11px`, boost `.pill.state-unknown` contrast to ≥ 4.5:1, and align `prototype.css` rules in place.

---

## Tasks

### Task 1 (`T1`, Size: S, `[Design/UI]`): Shared Typography, Status Pill Contrast & Top Nav Dropdown Triggers (COMPLETED)

- **Files:**
  - `dashboard/static/styles.css`
  - `dashboard/static/index.html`
  - `dashboard/static/app.js`
  - `tests/test_ui_top_nav_groups.py`
  - `tests/js/live_state_harness.cjs`
- **Changes:**
  1. **`dashboard/static/styles.css`:**
     - Add `system-ui, 'Segoe UI'` before generic family in `--font-display` (`'Big Shoulders Display', Impact, system-ui, 'Segoe UI', sans-serif`), `body` (`'IBM Plex Sans', -apple-system, BlinkMacSystemFont, system-ui, 'Segoe UI', sans-serif`), and `.mono` (`'JetBrains Mono', ui-monospace, system-ui, 'Segoe UI', monospace`). Keep `'IBM Plex Sans'` literal in `.tab-btn`.
     - Add `font-weight: 500;` to `body`.
     - Raise `.pill-label` and `.pill-detail` `font-size` from `9px` to `11px`.
     - Raise `.pill.state-unknown` text color from `var(--text-muted)` to `var(--text-secondary)` (`#94a3b8`, ≥ 4.5:1 contrast) and give `.pill.state-unknown` a dashed border (`border-style: dashed;`) so it remains visually distinct from `.pill.state-stopped`.
     - After `.pill.mode-stale`, add `.top-meta .pill[aria-haspopup]` (`border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; padding: 5px 10px; transition: border-color 0.15s ease, background-color 0.15s ease;`), `:hover`, `[aria-expanded="true"]`, `.top-meta .pill[aria-haspopup]::after` (`content: "▾"; font-size: 10px; opacity: 0.75; margin-left: 2px; display: inline-block; transition: transform 0.15s ease;`), and `.top-meta .pill[aria-expanded="true"]::after { transform: rotate(180deg); }`.
     - Add `#db-mode-badge .pill-detail { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: bottom; }` for graceful top-bar truncation.
     - Remove `font: inherit;` from `.services-pill-trigger` and remove `.services-caret` CSS rules (`lines 534–535, 576`).
  2. **`dashboard/static/index.html`:**
     - Change `#db-mode-badge` (`line 85`) from `<span ...>` to `<button id="db-mode-badge" class="pill state-unknown mono" type="button" title="Active registry" aria-haspopup="true" aria-expanded="false" aria-controls="db-run-switcher"><span class="pill-label">DB</span> <span class="pill-value">UNKNOWN</span></button>`.
     - Remove `<span class="services-caret" aria-hidden="true">▾</span>` from `#hud-services-pill` (`line 95`).
  3. **`dashboard/static/app.js`:**
     - In `closeRunSwitcher()`, also reset `aria-expanded="false"` on `#db-mode-badge`.
     - In `renderRunSwitcher()`, when setting `sw.style.display = 'block'`, set `aria-expanded="true"` on `#db-mode-badge`.
     - In `renderDbMode(status)`, move the `if (!el.dataset.wired)` click wiring block *before* `if (!mode)` and remove `el.style.cursor = 'pointer';`.
  4. **Tests (`tests/test_ui_top_nav_groups.py` & `tests/js/live_state_harness.cjs`):**
     - Add `test_header_pill_text_is_at_least_11px`, `test_font_stacks_have_system_fallback`, `test_dropdown_triggers_are_buttons_with_popup_attributes`, `test_dropdown_trigger_css`, and `test_db_badge_wires_before_unknown_early_return`.
- **Verification:**
  - `rtk pytest -q tests/test_ui_top_nav_groups.py tests/test_ui_top_nav_consistency.py tests/test_live_state_language.py`

---

### Task 2 (`T2`, Size: S, `[Design/UI]`): Port-Free Arm Labels, DB Run Switcher List Polish & Active Profile Indicator (COMPLETED)

- **Files:**
  - `dashboard/static/app.js`
  - `dashboard/static/styles.css`
  - `tests/test_live_state_language.py`
  - `tests/test_ui_top_nav_groups.py`
  - `tests/js/live_state_harness.cjs`
- **Changes:**
  1. **`dashboard/static/app.js`:**
     - In `runSwitcherLabel(run)`, remove `const portPart = run.dash_port ? ':' + run.dash_port : '';` and return `` `#${idxStr} ${t.arm}${pidPart}` ``.
     - Update `armRowHtml(arm, stagedName, activeName)` and `presetRowHtml(preset, stagedName, activeName)` to accept `activeName`, add `.active` class when matched, and render `<span class="arm-active-tag">✓ ACTIVE</span>` alongside `.arm-staged-tag`.
     - Update `profileOptionsHtml(options, pending, activeProfile = null)` to derive `activeName` (`arm:<name>` or `preset:<name>`) from `activeProfile` (`tournament_arm`, `preset`, or `name`) and pass it to `armRowHtml` / `presetRowHtml`.
     - Update `renderProfileOptions()` to pass `lastStatus?.active_profile || null` as the 3rd argument to `profileOptionsHtml`.
  2. **`dashboard/static/styles.css`:**
     - Widen `.db-run-switcher` (`width: 440px`), change `.db-run-row` from `grid-template-columns: 150px 1fr auto` to `grid-template-columns: minmax(0, 1fr) auto`, add `.db-run-row.db-demo-row { grid-template-columns: auto 1fr auto; }`, and add `.db-run-row:not(.db-demo-row) > .pill { width: 100%; border-radius: 4px; justify-content: flex-start; }` so run switcher rows show full run names cleanly without 150px mid-word clipping.
     - Add `.arm-row.active` and `.arm-active-tag` styles in `#profile-dropdown`.
  3. **Tests (`tests/test_live_state_language.py`, `tests/test_ui_top_nav_groups.py`, `tests/js/live_state_harness.cjs`):**
     - Update `test_run_switcher_label_formatting` in `tests/test_live_state_language.py` to expect `"#01 aggressive (pid 4242)"` and assert `":" not in res["tournamentWithPort"]`.
     - Extend `test_profile_picker_lists_view_and_start` and add `test_db_run_switcher_rows_do_not_truncate_at_150px` in `tests/test_ui_top_nav_groups.py`.
- **Verification:**
  - `rtk pytest -q tests/test_live_state_language.py tests/test_ui_top_nav_groups.py tests/test_ui_top_nav_consistency.py tests/test_dashboard_run_switcher.py`

---

### Task 3 (`T3`, Size: S, `[Design/UI]`): Connected Segmented Kanban Lane Switcher & Prototype Typography Alignment (COMPLETED)

- **Files:**
  - `dashboard/static/styles.css`
  - `dashboard/static/app.js`
  - `dashboard/static/prototype.css`
  - `tests/test_kanban_lanes.py`
- **Changes:**
  1. **`dashboard/static/styles.css`:**
     - Update `.kanban-lane-switcher`: `gap: 0; padding: 0; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-strong); background: rgba(0, 0, 0, 0.45);`.
     - Update `.lane-tab-btn`: `border: none; border-right: 1px solid var(--border-subtle); border-radius: 0; font-family: 'IBM Plex Sans', system-ui, 'Segoe UI', sans-serif;` and `.lane-tab-btn:last-child { border-right: none; }`.
     - Update `.lane-count-pill`: `font-size: 11px;`.
     - In `@media (max-width: 900px)`, add `.kanban-lane-switcher { width: 100%; } .lane-tab-btn { flex: 1 1 0; min-width: 0; justify-content: center; } .lane-tab-btn .lane-name { overflow: hidden; text-overflow: ellipsis; }`.
  2. **`dashboard/static/app.js`:**
     - In `window.setKanbanLane` (`line 11877`), change `btn.setAttribute('aria-selected', ...)` to `btn.setAttribute('aria-pressed', active ? 'true' : 'false')`.
  3. **`dashboard/static/prototype.css`:**
     - Edit existing rules in place (keeping `proto-` prefix on every selector): `.proto-sub-nav-btn` weight `500` → `600`; `.proto-sub-nav-btn[data-sub="regular"][aria-current="subpage"]` and `[data-sub="live"][aria-current="subpage"]` weight `600` → `700`; `.proto-body .ot-tab-btn` `font-size: 12px; font-weight: 600;`; `.proto-body .ot-tab-btn .ot-count` `font-size: 11px;`; `.proto-body .stitch-category-pill` `font-weight: 600;`; `.proto-body .pill.active` and `.proto-body .pill.reconnecting` `font-size: 11px;`.
  4. **Tests (`tests/test_kanban_lanes.py`):**
     - Add `test_lane_switcher_is_connected` and `test_set_kanban_lane_writes_aria_pressed`.
- **Verification:**
  - `rtk pytest -q tests/test_kanban_lanes.py tests/test_sidebar_prototype.py tests/test_dashboard_narrow_viewport.py`
  - Full UI suite check: `rtk pytest -q tests/test_ui_top_nav_groups.py tests/test_ui_top_nav_consistency.py tests/test_live_state_language.py tests/test_kanban_lanes.py tests/test_sidebar_prototype.py tests/test_dashboard_narrow_viewport.py tests/test_dashboard_run_switcher.py tests/test_ui_278_status_strip.py tests/test_ui_277_touch_targets.py`

---

## Run-Environment Caveats

| ID | Caveat | Owner | When |
| --- | --- | --- | --- |
| E1 | Primary checkout (`C:\Users\Tiger\Agents\Projects\AI Trading\spread-hunter`) is running a live 6-arm shadow tournament on ports `8801–8806` and holds untracked `configs/tournaments/i843-6arm-test_arms.json`. All #867 work and verification must run strictly inside worktree `C:\Users\Tiger\.worktrees\spread-hunter\i867` (and on an unused port such as `8817` if launching a temporary preview server) so the active tournament is never disturbed. | model | During Station III / Station IV |
| E2 | Tracked `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, and `tasks/todo.md` on `origin/main` (`98e959c7`) were leftovers from closed Issue #881 and were replaced in this worktree with Issue #867's contract and plan. | model | Completed in Station II (`0eaac7c2`) |


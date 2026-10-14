# SPEC — #504: Reorganize the top nav bar with consistent grouping and clear hierarchy

Branch: `i504/improve-reorganize-the-top-nav-bar-with-consistent` | Tier: Standard | Type: Design/UI + Code + UX/Copy

Supersedes #562 spec in this file's history.

## Goals

1. **Explicit Grouping & Semantic Structure**:
   - Header reads left-to-right as four distinct groups: Identity (`.brand`), Live State (`.top-group-state`), Data Source (`.top-group-source`), and Run Controls (`.top-group-controls`).
   - Child groups carry explicit ARIA semantics (`role="group"` with accessible labels `Live state`, `Data source`, `Run controls`).
   - Visual hierarchy uses clean inline-flex spacing (6px inner gap, larger inter-group gaps with vertical dividing borders; stronger divider before controls).
   - Tab switcher styling aligned with header styling (radius, padding, uppercase typography, active tint) while preserving `#95` / `#140` prototype hiding rules.

2. **Unified Pill Grammar**:
   - Standardize all status pills (`#market-scan-pill`, `#scan-state-pill`, `#master-status-indicator`, `#hud-services-pill`, `#hud-guardrail-pill`, `#db-mode-badge`, `#scan-engine-elsewhere`) to a single markup grammar:
     `.pill mono` base, optional pulse dot, `.pill-label` span, real whitespace, `.pill-value` span, optional `.pill-detail` span.
   - Introduce escaped helper in `app.js` (`renderPillHtml(label, value, detail, options)`) ensuring consistent HTML generation and preventing raw string overwrites that obliterate child spans.
   - Update Node fake DOM harnesses (`live_state_harness.cjs`, `master_toggle_harness.cjs`, `start_toggle_harness.cjs`) so `innerHTML` setters derive `textContent` cleanly by stripping tags and decoding basic XML entities (`&amp;`, `&lt;`, `&gt;`, `&quot;`, `&#39;`).

3. **Consistent Button Hierarchy**:
   - Introduce shared base class `.ctrl-btn` enforcing touch target (min-height `var(--touch-min)` >= 44px), typography, uppercase transform, 8px border radius, and unified disabled state.
   - Variants: `.ctrl-btn-primary` (weight 800, 16px horizontal padding for START / STOP) and `.ctrl-btn-secondary` (weight 700, 14px horizontal padding for SYNC VENUE, RESET).
   - Centralize master toggle class composition in `app.js` through a single helper function.
   - Keep `#btn-command-palette` in the controls group alongside run buttons.

4. **Dropdown Anchoring & Unified Placement**:
   - Move `#services-dropdown` from within `.services-pill-host` to be a direct child of `<header>`, preventing clipping by `.top-meta`'s horizontal scroll container.
   - Unify positioning logic via `placePanel(trigger, panel)`: calculate top/left relative to `<header>`, clamp left edge with >=4px viewport margin, reposition dynamically on strip scroll and window resize.
   - Synchronize open state, outside-click dismissal, and Escape-key closure (opening one panel automatically closes the other).

5. **Live State Aggregation & Neutral Kanban Hero Palette (Consolidated #479)**:
   - Provide summary aggregate status pill where live state group collapses five service pills to worst-state-wins (DOWN > DEGRADED > RUNNING) with detail popover listing all services, state pills, and heartbeat timestamps.
   - Retain DB badge, shadow clock, and exposure bar always visible in the source group.
   - Recolor kanban bucket heroes to neutral tones (gray/ink chips), retiring categorical colors (`#a78bfa`, `#fb923c`, `#f472b6`, `#38bdf8`, `#fbbf24`), reserving green/amber/red/gray strictly for live execution states.

## Out of Scope

- No changes to Python backend servers (`server.py`), JSON/database schemas, or trading execution logic.
- No modifications to `data/orders.db` (production registry).
- Do not press the live START button.
- No framework migrations or external npm runtime dependencies.

## Resolved Open Questions (from issue & code)

1. **Aggregate pill label**: Shows worst single state only (e.g. `DOWN` or `DEGRADED`); individual service counts live inside the popover.
2. **Kanban hero styling**: Drop categorical hues to neutral monochrome chips; no new icon set needed.
3. **`#btn-command-palette`**: Positioned inside `.top-group-controls` alongside `#btn-master-toggle`, `#btn-sync`, `#btn-reset`.

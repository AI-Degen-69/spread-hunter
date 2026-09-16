# Design System — Spread Hunter Live

> **The single source of truth for every visual and UI decision in this repo.**
> Read this file before touching `dashboard/static/` or any operator-facing page.
> Do not deviate without explicit owner approval, and record any approved
> deviation in the Decisions Log at the bottom.

## Product Context
- **What this is:** real-money execution engine + operations dashboard for the
  Polymarket spread hunter strategy (buy UP+DOWN < $1.00, merge back to USDC).
- **Who it's for:** a single expert operator running live capital.
- **Space/industry:** trading terminals and industrial control rooms — *not* SaaS
  admin panels.
- **Project type:** internal ops dashboard, data-heavy, multi-page.

## Design Thesis
**"Nothing surprises me."** Color means state. Type means data. The only motion
in the system is a heartbeat. The dashboard must be the *first* to announce that
something is wrong — a dead process, a stale feed, its own dead backend — before
the operator notices anything else. Broken-looking beats lying.

## Aesthetic Direction
- **Direction:** Industrial control-room. **Dark-only** — this is a console used
  for hours at a time; there is no light mode.
- **Decoration level:** minimal. No gradients, no glow, no breathing or pulsating
  animations. Color is never decorative: **every colored pixel is a state.**
- **Mood:** calm precision under live money. Terminal-grade density, zero noise.
- **Status surfaces:** no emoji. Solid high-contrast pills. Typed-confirm modals
  for every destructive action (EMERGENCY CANCEL ALL, RESET).

## Layout — Sidebar Shell
Three fixed zones on every page:

1. **Nav rail** (left): 56px collapsed, expands to 236px on hover, pinnable
   (`sh-proto-rail-pinned`). Below 900px it becomes a drawer with a hamburger
   toggle beside the wordmark. Page order mirrors the money pipeline:
   **Dashboard → Data & Markets → Strategy → Trades & Positions → Reports & Analytics.**
   External links (Polymarket, repository) sit at the bottom of the rail.
2. **Status strip** (top header, persistent on every page): brand + wallet,
   DB-mode badge, shadow-run clock, USDC balance, exposure bar, SYNC, RESET,
   EMERGENCY CANCEL ALL.
3. **Content column**: max-width 1900px on wide screens; pages show exactly one
   panel group at a time (`#page-*` sections), chosen page persists
   (`sh-proto-page`).

Rules:
- The rail is the only navigation. No competing tab rows.
- A research page may live on its own path — it keeps its own poll loop and
  carries no control token — but it must carry the shared header, tokens,
  fonts, and the live-state language. No research page is served today:
  `/tape` and `/reversion` were removed once the forward test returned
  NO_SIGNAL on every cell (#231).
- The emptied `#tab-1..3` shells stay in the DOM: `app.js` toggles them by id and
  reads `#tab-3.hidden` before scrolling the kanban.

## Typography
- **Display:** Big Shoulders Display 700/800 — brand face, condensed industrial
  headers, page titles, card titles (`.font-display`).
- **Body/UI:** IBM Plex Sans 400/500/600 — replaced Inter; designed for technical
  consoles. Falls back: `-apple-system, BlinkMacSystemFont, sans-serif`.
- **Data:** JetBrains Mono 400–700 with `font-variant-numeric: tabular-nums`
  (`.mono`). **ALL** money, prices, sizes, P&L, percentages, timestamps, PIDs,
  ages, and counts that are compared across rows/columns are monospace
  tabular. Never set a comparing number in the body face.
- **Loading:** Google Fonts CDN `<link>` (one request, all three families).
- **Scale (px):** 10 (micro labels) · 11 (secondary/meta) · 12 (mono data) ·
  13 (body base) · 15 (section titles) · 18 (page title in header) · 28
  (hero values). Nothing larger except the portfolio equity value.
- **Letter-spacing:** display faces 0.02–0.06em; uppercase micro-labels 0.04em.

## Color
- **Approach:** restrained. Slate base; four semantic hues + quiet gray.
  A hue may never appear without a state meaning behind it.

| Token | Value | Role |
|---|---|---|
| `--bg-base` | `#080c14` | page background |
| `--bg-surface` | `#0f172a` | header, rail, opaque panels |
| `--bg-surface-raised` | `#1e293b` | raised tiles, active tab |
| `--bg-card` | `rgba(15,23,42,0.65)` | content cards |
| `--border-subtle` | `rgba(255,255,255,0.08)` | card borders |
| `--border-strong` | `rgba(255,255,255,0.16)` | hover/focus borders |
| `--text-primary` | `#f8fafc` | headings, key values |
| `--text-secondary` | `#94a3b8` | body copy |
| `--text-muted` | `#64748b` | labels, metadata, UNKNOWN/STOPPED |

- **Semantic (the only saturated colors on screen):**
  - **green** `#10b981` — running / healthy / profit (`--signal`, `--green-ok`)
  - **amber** `#f59e0b` — degraded / stale / warning (`--warn`, `--amber-warn`)
  - **red** `#ef4444` — down / error / loss / destructive (`--loss`, `--red-alert`)
  - **cyan** `#38bdf8` — info / open / neutral highlight (`--open`, `--blue-rest`)
  - **gray** `--text-muted` — stopped is **quiet gray, not red**: a deliberately
    stopped service is not an alarm.
- Semantic pills pair each hue with its `--*-bg` wash and `--*-border` token
  (`--green-bg/-border`, `--red-*`, `--amber-*`, `--blue-*` in `styles.css`).

## Live-State Language (the watchdog system)
One vocabulary, applied identically to every process, feed, connection and tile.

| State | Meaning | Color | Dot | Label |
|---|---|---|---|---|
| `RUNNING` | heartbeat fresh within cadence | green | 1 Hz blink | RUNNING |
| `DEGRADED` | heartbeat stale but under the red threshold | amber | static | DEGRADED |
| `DOWN` | heartbeat beyond the red threshold / process gone | red | static | DOWN |
| `STOPPED` | intentionally not running | gray | none | STOPPED |
| `UNKNOWN` | never seen / registry unreadable | gray pill | none | `--` |
| `STALE` | the page lost backend contact | amber banner | none | DATA STALE — last seen HH:MM:SS |

Implementation contract (`app.js` exports, `styles.css` styles):
- **`stateKey(input, thresholds)`** maps `(running, age_sec)` → one of the six
  states. Thresholds are per-service cadence: a 5s loop goes DEGRADED at 3×
  cadence (15s) and DOWN at 12× (60s); a ~5s watcher uses the same defaults;
  callers may override (`{degraded, down}` seconds).
- **`statePillHtml(state, ageSec)`** renders the canonical pill: state class +
  heartbeat age (e.g. `RUNNING · 3s`). Age text is omitted for STOPPED/UNKNOWN.
- **Heartbeat age is displayed, not implied.** Every live indicator shows seconds
  since its last heartbeat, aging through the green→amber→red ramp.
- **Backend-contact watchdog:** `pollStatus()` tracks consecutive failures and
  last-success time. On failure it must NOT keep the last render pretending to
  be live: it flips the page to the STALE banner (`#backend-contact-banner`)
  and every live tile shows its last-seen age. Recovery restores rendering and
  clears the banner. The banner must exist in `index.html` and be driven by
  `setBackendContact(ok, lastSeenMs)`; the render functions stay reachable when
  offline (they use `lastState`/`lastKpi`).
- **A pill names the process it measures.** A liveness pill placed beside
  unrelated copy is read as reporting that copy. The top-nav
  `#market-scan-pill` reports the Market Filter PROCESS
  (`scripts.filter_loop`): green when the process is alive and
  `runtime/pipeline.json` is within two scan cycles, amber when the process is
  alive but the snapshot is stale or absent, red only when no scanner process
  is running, gray when the process registry cannot be read. It sits in the top
  nav so the answer is on screen from every tab.
- **Ramps are calibrated on measured cadence, never on configured cadence.** A
  configured `--interval` is the sleep between rotations, not the length of
  one. `/api/scan-state` publishes `cadence_sec` (elapsed run ÷ rotations
  completed) and the page ramps off that; the configured interval is only the
  floor.
- **1 Hz blink is the only animation in the system.** `.pulse-dot.blink` steps
  opacity at 1 Hz while RUNNING; death is visible as absence of motion.
  `prefers-reduced-motion: reduce` renders the blink as a static green dot.
  No other looping animation is allowed anywhere.

## Spacing
- **Base unit:** 4px. **Density:** compact.
- **Scale:** 2 / 4 / 8 / 12 / 16 / 24 / 32 / 48. Whitespace zones sections;
  it never pads cards out.

## Radius
sm 4px (inputs, chips) · md 8px (buttons, tiles) · lg 12px (cards, rail) ·
pill 9999px (status pills, exposure bar).

## Motion
- **Approach:** minimal-functional. The 1 Hz heartbeat blink is the only
  *liveness* loop.
- **Allowed exceptions (state feedback, not decoration):** the loading-skeleton
  shimmer (exists only while data is loading) and the SYNC button's busy pulse
  (exists only while a sync runs). Both end; neither implies a state the data
  doesn't support.
- **Retired:** glow-breathe / pulsating status pills (decorative).
- **Easing:** ease-out (enter) · ease-in (exit) · ease-in-out (move).
- **Durations:** micro 80ms · short 180ms · medium 300ms. Rail expansion uses
  220ms `cubic-bezier(0.16, 1, 0.3, 1)` (existing rule).
- **Never:** breathing/pulsating loops on status surfaces, decorative entrance
  animations, parallax, glow pulses.
- All loops honor `prefers-reduced-motion: reduce`.

## Accessibility
- Keyboard reachability of the rail without the pointer holding it open
  (`:has(:focus-visible)`, never `:focus-within` on the rail).
- Collapsed rail labels are clipped, never removed or `display:none`.
- Touch targets ≥ 44px (`--touch-min`).
- Every icon-only control carries its visible label in `title` and stays in the
  accessibility tree.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-09-13 | Initial design system created | /design-consultation: owner confirmed product (single-operator real-money console), memorable thing "nothing surprises me", sidebar shell adopted (already live at `/`), all three watchdog risks adopted (heartbeat ages, backend-contact watchdog, 1Hz blink), IBM Plex Sans body font, preview-first workflow |
| 2026-09-16 | Top-nav `MARKET SCAN` pill; watchdog ramps read measured cadence | Owner: "I thought the pill expressed the status of the LOOP SCRIPT that scans markets." It did not — the Data & Markets pill reports the trading loop's heartbeat, and sitting beside `last scan: 3m ago` it read as a dead scanner. Two changes: (1) a new `#market-scan-pill` in the top nav reports the Market Filter process itself, visible from every tab; (2) the shadow heartbeat now records its rotation count, so `read_shadow_run` ramps off the cadence a rotation really costs (~160s here) instead of the configured 5s, which was painting a healthy loop red every cycle. |
| 2026-09-15 | `LIVE ONLY` scope tag on the two live-stack-only service cards | Owner asked for a label so the two permanently-STOPPED cards during a shadow rehearsal read as out-of-scope, not dead. Scope is not state and never changes at runtime, so it stays outside the live-state colour vocabulary: quiet gray text with a dashed border (`.svc-scope-pill`), sat beside the existing role tag rather than replacing it. The `title` names `core_brain.shadow_run` as what covers those services during a rehearsal. |

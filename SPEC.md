# SPEC — #586: Unify top-nav pill grammar across header, services dropdown, and DB switcher

Branch: `i586/unify-top-nav-pill-grammar-across-header` | Tier: Small | Type: Design/UI + Code

Follow-up to #504 (shipped as #575, merge `942a8e9`). Operator review of the live header found four inconsistencies; this spec freezes the single grammar that resolves all four.

## Goals

1. **One grammar, three surfaces**:
   - Every pill/row reads as `label` + `value` + `detail`:
     - `label`: service or mode name — gray (`--text-muted`), 9px, weight 700, uppercase, letter-spacing 0.05em. Never colored.
     - `value`: state only (`RUNNING / STOPPED / DOWN / DEGRADED / UNKNOWN`, plus `FINISHED / ENDED` in the switcher) — 11px, weight 700, colored by state: green `#34d399` / gray `var(--text-secondary)` / red `#fca5a5` / amber `#fbbf24` / muted.
     - `detail`: meta only — muted 9px, `max-width` with ellipsis, never a sentence.
   - Surfaces: header pills (`#master-status-indicator`, `#db-mode-badge`, `#hud-services-pill`), SERVICES dropdown rows (`renderServicesDropdown`), DB run-switcher rows (`renderRunSwitcher`, `demoSwitcherRow`).

2. **Mode rides on tint, not text**:
   - `mode-live` (emerald wash) vs `mode-shadow` (amber wash) stays as background/border on the pill shell; the *text* color carries state only.
   - Resolves the double-meaning amber: `SHADOW STOPPED` reads as label SHADOW (gray) + value STOPPED (gray) on an amber-wash shell; `DB SHADOW` reads as label DB (gray) + value SHADOW-mode on amber-wash shell with filename detail. Shell = where the numbers come from; text = whether it is running.
   - Applies to header pills, dropdown row pills, and switcher row pills alike.

3. **Fixed meta vocabulary**:
   - Allowed detail values: `pid <n> · <uptime>`, `N alerts`, `auto-watch`, `stopped`, `rehearsal ended`, filename (`demo_orders.db`), heartbeat age (`· 12s`). Nothing else.
   - The `live only (shadow uses in-process client)` / `shadow only (live uses venue engine)` explanations move from the meta slot to the row's `title` tooltip. Guardrail keeps `N alerts` / `auto-watch` / `telemetry down`.

4. **Dropdown rows become a grid**:
   - `pill (fixed 150px) | name (flex, ellipsis) | meta (fixed 140px, right-aligned, ellipsis)`.
   - Long meta can never squeeze names into truncation or wrap to two lines. Panel width 340px → 380px to fit the fixed columns.

5. **Dot spacing**:
   - Dropdown row pills go through `renderPillHtml` (or equivalent gap) so dot/label/value spacing matches header pills. Fixes ENGINE dot touching the D.

## Out of Scope

- No change to which services exist (`SERVICE_DEFS`), to state computation (`stateKey`, `processState`, `aggregateServiceState`, worst-state aggregation), or to panel placement/dismissal (`placePanel`, `placeRunSwitcher`, outside-click/Escape).
- No changes to Python backend (`dashboard/server.py`), JSON/database schemas, or trading execution logic.
- No modifications to `data/orders.db` (production registry).
- Do not press the live START button.
- No framework migrations or external npm runtime dependencies.

## Resolved Open Questions (from issue & code)

1. **DB value wording**: keeps `SHADOW`/`LIVE` (registry mode) as the value; disambiguation comes from the shell tint + filename detail + tooltip, not a rename. Renaming to `REHEARSAL` would break `renderDbMode`/`dbModeVerdict` consumers and the `test_db_badge_renders_grammar` pin.
2. **Switcher FINISHED/ENDED**: kept as values (they are run states, not service states); they reuse the stopped gray tone, never a new hue.
3. **Demo row**: adopts the same pill grammar (`DEMO` label + `LOADED`/`AVAILABLE` value) rather than staying a bare button row.

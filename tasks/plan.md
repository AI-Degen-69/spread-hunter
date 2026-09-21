# Plan: Issue #259 — Equity tooltip shows trade facts

Size: **Standard** (4 files across backend + frontend + harness + tests, one architectural decision: compute facts from in-memory data vs new queries — chosen in-memory).
Task type: **Code + Design/UI** (backend plumbing + tooltip rendering).

## Context
Hovering an equity-curve point shows a raw condition_id hex (`Market: 0x6054…`)
with zero information value. Every requested fact already exists in the
registry — `_resolve_market_meta()` resolves titles, `closes` carries
`cost_basis` + `method`, `quotes.ts` gives the first-seen stamp — but the
equity path never wires them to the tooltip (`kpi.py:1892-1898`,
`app.js:1451-1511`, `app.js:1656-1663`).

## Spec (see SPEC.md)
Goals, acceptance, interface contracts, edge cases and out-of-scope copied to
`SPEC.md` for the Standard size. Headline: each close point carries
`title`/`cost_basis`/`method`/`hold_seconds`, and the tooltip renders all four
rows with `--` fallbacks instead of fabricated values.

## Tasks

### Task 1 — Backend: enrich close points with trade facts [Backend/Logic] [x]
- **Files:** `core_brain/kpi.py` (`report()`, equity loop ~1826-1898)
- **Build:** before the loop, build a per-`condition_id` title lookup via
  `_resolve_market_meta()` (once per market) and an earliest-`quotes.ts`
  lookup from the run-filtered quotes; on each close point append `title`,
  `cost_basis` (copied), `method` (copied), `hold_seconds`
  (`close.ts - first_quote_ts`, `None` when missing/negative). Mark points
  untouched; no new queries, no schema change.
- **Skill:** test-driven-development
- **Verification:** focused pytest on `tests/test_portfolio_card_basis.py`
  plus a new backend assertion on the enriched fields; new test fails without
  the change.

### Task 2 — Frontend: passthrough + method badge [Design/UI] [x]
- **Files:** `dashboard/static/app.js` (`buildBrokerEquitySeries()` both
  ALL + windowed branches, `METHOD_BADGES`/`methodBadge()` near `gateBadge()`)
- **Build:** conditional-copy `title`, `cost_basis`, `method`,
  `hold_seconds` onto close points (present-only, old fixtures unchanged);
  add `methodBadge()` mapping `merge`/`shadow_merge` → `MERGED` with neutral
  fallback for unknown/missing; export via `module.exports`.
- **Skill:** frontend-ui-engineering
- **Verification:** node harness asserts `chart_series` carries the four
  fields when present and keeps the old shape when absent.

### Task 3 — Frontend: render the four tooltip rows [Design/UI] [x]
- **Files:** `dashboard/static/app.js` (tooltip `innerHTML` ~1658-1672)
- **Build:** Market row prefers `data.title` over `data.market` (escaped);
  add P&L % row (`pnl / cost_basis`, `--` when unmeasurable, `fmtPct()` +
  sign color); add Method row (`methodBadge`, only when present); add Held
  row (order-age format, `--` when missing). Existing
  `broker-tooltip-row` markup, short labels, `tooltipW` bump only on
  overflow, badge reuses `.param-badge`.
- **Skill:** frontend-ui-engineering
- **Verification:** harness `tooltip_html` contains title (not raw hex),
  percent, `MERGED`, and hold text on the fixture close.

### Task 4 — Harness: capture tooltip on simulated hover [Code] [x]
- **Files:** `tests/js/portfolio_card_harness.cjs`
- **Build:** stub `querySelector('#broker-svg-chart' / '#broker-crosshair-line' /
  '#broker-crosshair-dot')` returns working elements; `addEventListener`
  stores callbacks; fire synthetic `mousemove`, read
  `broker-chart-tooltip` `innerHTML` into new `tooltip_html` output field
  (pattern from `markout_chart_harness.cjs`).
- **Skill:** test-driven-development
- **Verification:** harness output includes non-empty `tooltip_html` for a
  close point; previously empty/missing.

### Task 5 — Tests: pin rows + fallbacks [Code] [x]
- **Files:** `tests/test_portfolio_card_basis.py`
- **Build:** extend close fixtures with the four fields; assert title (not
  hex), percent, badge, hold in `tooltip_html`; add missing/zero
  `cost_basis` → `--` case and missing `hold_seconds` → `--` case; keep
  existing exact-match `chart_series` assertions green.
- **Skill:** test-driven-development
- **Verification:** focused `tests/test_portfolio_card_basis.py` green, with
  the new tests failing on the pre-change code (RED confirmed).

## Improvement proposal (adopted by default)
Show hold time as a two-part `3h 12m` value in the tooltip instead of the
current `fmtOrderAge()` single largest unit (`3h`), because the issue's own
acceptance example reads `e.g. 3h 12m` and `app.js:3373` today truncates to
one unit.

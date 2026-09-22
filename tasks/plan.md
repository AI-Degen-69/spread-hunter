# Plan — Issue #272: Active Markets tab shows IDLE for markets being quoted

## Spec (concise — Small tier)
**Goal:** the STATUS pill in the Active Markets tab reflects *quoting activity*, not resting-order
presence. A market whose quotes are live but whose orders have not rested (or were
cancelled/filled) must not read IDLE.
**Vocabulary (operator-facing):**
- `QUOTING` — engine is actively quoting this market (`quotes_count > 0` in `kpi.by_market`).
- `RESTING` — QUOTING **and** at least one order resting on the book (more specific state;
  same pill family, distinct label). Recommended by the issue's open-question default.
- `IDLE` — reserved; must not appear in the Active Markets tab (everything listed there is
  being quoted by definition of the tab filter `isQuotedMarket`).
- Orders-table pill (`app.js` market rows, `marketRowPairHtml`) keeps `QUOTING`/`IDLE` derived
  from `quotes_count` — unchanged; consistency requirement is that IDLE means the same thing
  (no quote activity) everywhere.
- Tooltip/legend: column header `STATUS` gets a `title` explaining the two states.
**Out of scope:** fleet-level SCANNING/IDLE/STALLED banner (`dashboard/server.py`
`compute_scan_state`), quoting behaviour, order execution logic, any backend change.

## Task type & size
- **Type:** Code (UI logic). **Size:** Small — one file (`dashboard/static/app.js`) + tests.

## Interfaces
No API/payload change. Pure frontend derivation in `activeMarketsRows`
(`dashboard/static/app.js` ~:4060-4085) from inputs already present: `m.quotes_count`,
`ordersByMarket[cid]`, `isRestingOrder`.

## Tasks

### T1 — Status derivation helper [Backend/Logic]
- **Target files:** `dashboard/static/app.js` (`activeMarketsRows`)
- **Build:** extract `marketStatusPill(m, restingHere)` returning pill HTML:
  resting order → `RESTING` (pill `quoting-breathing`), else `quotes_count > 0` → `QUOTING`
  (pill `quoting-breathing`), else `IDLE` (pill `stopped`). Pure function, exported for tests.
- **Helper skill:** `test-driven-development`
- **Depends on:** —
- **Verification:** new RED→GREEN test in the JS harness / pytest static test asserting a
  quoted-but-not-resting market renders QUOTING, not IDLE.

### T2 — Wire into Active Markets tab + header tooltip [Design/UI]
- **Target files:** `dashboard/static/app.js` (`activeMarketsRows`, `OT_COLUMNS` header for
  active-markets view)
- **Build:** use `marketStatusPill` in the tab rows; add `title` on the STATUS column header
  explaining QUOTING / RESTING.
- **Depends on:** T1
- **Verification:** harness test on rendered row HTML.

### T3 — Focused verification [Code]
- **Target files:** tests only
- **Build:** run the focused dashboard tests
  (`python -m pytest -q tests/test_orders_trades_table.py tests/test_dashboard_input_lag.py`
  per the issue's acceptance criteria, plus any new test file); hands-on dashboard check.
- **Depends on:** T2
- **Verification:** focused suites green.

## Improvement proposal (evidence-based, adopted by default — simplification)
Evidence (issue #272, Open questions): *"Should a market with live quotes but no resting order
read as QUOTING (recommended) or get its own intermediate label? Default assumption: QUOTING,
with the resting-order state as a separate, more specific pill."* → Adopted: introduce `RESTING`
as the more specific pill on top of QUOTING rather than a third ambiguous state. Recorded here;
drops only on explicit operator rejection.

## Resolved open questions (from code)
- Data source for "actively quoting": `kpi.by_market[cid].quotes_count` — already present in
  `activeMarketsRows` via `m` and used by the sibling pill at `marketRowPairHtml`; no new API
  surface needed. Confirmed by reading `dashboard/static/app.js` (~:4060, ~:4513).

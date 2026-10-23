# SPEC — Issue #783: Orders & Trades: dedicated Category column, slimmer rows, Active Markets cleanup

## Goals
- Each of the four stitch tabs (Active Markets, Orders, Open Positions, Closed Trades) shows a dedicated,
  sortable `CATEGORY` column immediately right of the Market column.
- The category pill renders exactly once per row — in the new cell, never under the market title.
- Rows read visibly shorter; headers keep their current legibility.
- Active Markets loses the EDGE column (header + cell + dead math) and the 24H VOLUME cell shows volume only.

## Acceptance criteria
1. Four tabs each render a `CATEGORY` `<th>` with a working sort button directly after the Market header.
2. Clicking CATEGORY sorts A→Z on first click, Z→A on second, in every tab.
3. No `stitch-category-pill` remains inside any `stitch-market-cell` block; exactly one pill per row.
4. `Uncategorized` still renders (muted chip) so column alignment never leaves a hole.
5. Active Markets has no `EDGE` header/cell text and no `Nd` horizon line; volume cell keeps the `$152k`-style amount.
6. Empty-state colspans match the new column counts (Active Markets 7, Orders 6, Positions 9, Closed Trades 7);
   the Closed Trades ledger expand row spans all 7 columns.
7. Header vertical padding stays `12px`; body cells read shorter than today's `14px`.

## Column contracts (locked — see tasks/plan.md Step 4)
| View | Header order | Category sort key | Empty colspan |
|---|---|---|---|
| Active Markets | MARKET, CATEGORY, YES PRICE, NO PRICE, 24H VOLUME, STATUS, LAST UPDATED | 2 (existing case, already text) | 7 |
| Orders | Market & Arbitrage Status, CATEGORY, Dual-Leg Quotes, Total Committed, LAST UPDATED, Actions | 8 (new; added to `OT_TEXT_COLUMNS['open-orders']`) | 5 → 6 |
| Positions | MARKET & CLASSIFICATION, CATEGORY, HELD INVENTORY, TOTAL COST, CURRENT PRICE, MARK VALUE, UNREALIZED P&L, HEDGE STATUS, LAST UPDATED | 2 (new case; key free in this switch, already in text set) | 8 → 9 |
| Closed Trades | MARKET, CATEGORY, TOTAL COST, OUTCOME BREAKDOWN, REALIZED P&L, CLOSED AT, LEDGER | 2 (existing case, already text) | 6 → 7 (incl. expand row) |

Sort keys are stable logical keys, never column positions: no existing key is renumbered or reused.

## Edge cases
- Unknown category → muted `Uncategorized` chip (current `marketCategory` fallback, unchanged).
- Positions event line: keep the rule that hides the event text when it equals the category.
- Orders rows keep dot / meta tag / arbitrage badge in the Market cell; only the pill moves.
- Positions rows keep the activity pill and event line; Closed Trades rows keep winner badge, reason chip, fills count.
- `days_to_resolve` sort (key 8, Active Markets) keeps reading the field directly — untouched by the math removal.
- The shared harness returns the legacy header for classic assertions; new `stitchHead` field carries the stitch header.

## Explicit out of scope
- Legacy non-stitch path (`otHeadHtml` / `ordersTradesRows`) — untouched.
- Renaming the existing Market header labels (`MARKET & CATEGORY`, `MARKET & CLASSIFICATION`) — left as-is.
- Manual `?v=` bumps in `index.html` — the server fingerprints static assets on serve, so no hand edit is needed.
- No backend/API change: all data (`volume_24h`, `marketCategory(m)`) already arrives in the KPI payload.
- No new dependencies.

# Backlog — Issue #784 (Orders tab: status-only pill column + dedicated Pair Cost column)

Out-of-scope candidates noticed during the build. Station III records them; only
Station VI resolves a row. An `open` row is never deleted.

| # | Candidate | Found in | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | The classic Orders table (non-stitch) still renders the old 8-column layout and is untouched by this change; it shares `OT_TEXT_COLUMNS['open-orders']` with the stitch table. The new STATUS (9) and PAIR COST (10) keys were added to the stitch builder only; the classic path never reads them. | Station III | `dashboard/static/app.js` `otHeadHtml`/`ordersTradesRows` use keys 0–7 | dismissed | The ticket scoped the change to the stitch terminal (the surface `renderOrdersTrades` paints). The classic table is a legacy path; if it is ever reconciled with the stitch layout it should be its own ticket. |
| N2 | `sortIdx` keys are per-view and view-scoped; #783 already took key 8 (CATEGORY) in the stitch Orders header, which forced this change onto keys 9 (STATUS) and 10 (PAIR COST). Any future column added to the stitch Orders table must take key 11+ to avoid renumbering saved sorts. | Station III | `stitchOpenOrdersHeadHtml` cols; `stitchOpenOrdersRows` switch | dismissed | Documented guard, not a defect: no Orders stitch column has been added since #784, so keys 11+ stay free and no saved sort can be renumbered today. Re-open with its own ticket when a new Orders stitch column is actually added. |
| N3 | The "Total Committed" header column body still renders the break-even max (cap minus resting price), not a committed-cost figure; the mismatch predates this change and was left as-is. | Station III | header `Total Committed` (key 5) vs row "Break-even max" cell | dismissed | Outside this ticket's scope; the header/body naming mismatch is a pre-existing cosmetic issue. Re-open with its own ticket if the column should actually show committed cost. |

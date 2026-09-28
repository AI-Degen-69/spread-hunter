Branch: i294/sort-orders-trades-columns | Issue: #294

# Plan: Issue #294 â€” Click-to-sort the Orders & Trades tables

**Size tier:** Standard â€” one JS file plus one CSS file plus the Node harness and the focused suite, with one architectural decision (sort unit = backing group, not row). **Task type:** Code + UX/Accessibility (Design/UI).

**Stack (auto-detected):** vanilla ES2020 in `dashboard/static/app.js`, plain CSS, Node v24 harness (`tests/js/orders_trades_harness.cjs`), pytest wrapper (`tests/test_orders_trades_table.py`).

## Dependency graph
```
T1 (sort model + comparators) --> T2 (builders sort backing groups) --> T3 (header controls + a11y) --> T4 (CSS affordances)
        |                                        |                              |
        +-------------------------------------- +------------------------------+--> harness pass-through (part of T1/T2)
```
T2 cannot be written before T1 (no comparator). T3 is independent of T2's internals but is verified against T2's rendered output, so it runs after. T4 is styling only and is last so it can be checked against the real markup T3 emits.

## Tasks

### T1 â€” Sort model and comparators (risk-first: the semantics live here)
- **Size:** M | **Domain:** [Backend/Logic] | **Helper:** `test-driven-development`
- **Files:** `dashboard/static/app.js`, `tests/js/orders_trades_harness.cjs`, `tests/test_orders_trades_table.py`
- **Build:** `otSortSpec(view)` returning one spec per `OT_COLUMNS[view]` entry â€” `{kind: 'text'|'number', get(item, ctx)}` â€” plus a stable `compareOtv` that ranks unmeasured (`null`/`undefined`/`--`) last in both directions and compares numerics by value, not by formatted string. Add the optional `sort` input passthrough to the harness and export both helpers from `module.exports` (`app.js:5424-5431`).
- **Depends on:** â€”
- **Verify:** `python -m pytest -q tests/test_orders_trades_table.py` â€” new tests assert `$1,000.00` outranks `$95.00`, 9 ranks below 10, `1.5d` above `10.0d`, and that a `--` cell ranks last both ascending and descending. RED before the change.

### T2 â€” Sort the backing groups in all four builders
- **Size:** M | **Domain:** [Backend/Logic] | **Helper:** `test-driven-development`
- **Files:** `dashboard/static/app.js`, `tests/test_orders_trades_table.py`
- **Build:** thread `sort` through `ordersTradesRows` (`:4425`) into `activeMarketsRows` (`:4093`), `openOrdersRows` (`:4225`), `positionsRows` (`:4365`), `closedTradesRows` (`:4313`); each sorts its groups/entries **before** mapping to rows. Pair grouping stays intact: `groupOrdersByPair` groups (`:4140`), `heldMarketEntries` (`:4278`), `closedTradesEntries` (`:4304`). When `sort` is null, the existing comparator and output are untouched. `renderOrdersTrades` (`:4432`) reads per-view state and passes it to both `otHeadHtml` and `ordersTradesRows`.
- **Depends on:** T1
- **Verify:** focused suite â€” pair rows stay adjacent with `ot-pair-start`/`rowspan` anchored after sorting Orders and Positions; a CLOSED TRADES main row keeps its expanded sub-row beneath it; and with no sort every view's order is byte-identical to the pre-change output.

### T3 â€” Sortable header controls and accessibility
- **Size:** S | **Domain:** [Design/UI] | **Helper:** `frontend-ui-engineering`
- **Files:** `dashboard/static/app.js`, `tests/test_orders_trades_table.py`
- **Build:** `otHeadHtml(view, sort)` emits a `<button type="button">` in every `<th>`, the active column's `<th>` carries `aria-sort` and a direction indicator, and clicking the same column flips direction while a different column starts fresh. One delegated `click` listener on the persistent `#orders-trades-head`, wired in `initOrdersTradesTabs` (`:4477`), so it survives every 2s `innerHTML` rewrite. Per-view state map `{col, dir}` in memory.
- **Depends on:** T2
- **Verify:** focused suite â€” `otHeadHtml` output contains a real button per column, `aria-sort` appears on exactly one `<th>`, and `test_active_markets_status_header_explains_the_vocabulary` (`:930-945`) still passes unmodified. Hands-on: open the dashboard, click a header, click it again, switch tabs and come back.

### T4 â€” Header affordances in CSS
- **Size:** XS | **Domain:** [Design/UI] | **Helper:** `frontend-design`
- **Files:** `dashboard/static/styles.css`
- **Build:** hover and `:focus-visible` states plus `cursor: pointer` for the sortable header, using existing `DESIGN.md` tokens (`--border-strong`, `--text-secondary`, `--text-primary`) following the `.toggle-cancelled-btn` pattern at `styles.css:702-711`. The `.ot-market-head` width floor (`:1353`) keeps matching.
- **Depends on:** T3
- **Verify:** `python -m pytest -q tests/test_orders_trades_table.py` still green; hands-on check of hover, focus ring and the direction indicator in the browser.

## Checkpoints
- **After T1:** comparators proven against the `--`/numeric edge cases.
- **After T2:** all four views sort with pair structure intact and no-sort output unchanged.
- **After T3+T4:** browser pass â€” click, flip, tab switch, 2s re-render.

## Assumptions resolved at plan time
- **Sort unit is the backing group, never the DOM row** â€” the issue's central structural constraint, confirmed against `rowspan` at `:4257-4258` and `:4393-4399` and the sub-row at `:4573-4580`.
- **Issue line numbers are stale** and were re-verified; anchors in this plan are the checked ones.
- **In-memory state only**, no new `localStorage` key â€” `OT_STORAGE_KEY` keeps meaning "which view".
- Baseline is green (62 passed) and Node is present, so the harness is not skipped.


## CodeRabbit plan intake (costed once â€” do not re-read the comment)
- **Adopted:** sort backing groups before pairing; in-memory per-view state; one delegated listener on the persistent `<thead>`; native `<button type="button">`; all columns sortable with fixed first-click direction; additive columns by group sum, measurement columns by the extreme leg in sort direction.
- **Rejected:** its stale file/line anchors; its per-builder accessor threading, folded into a single `otSortSpec` table.
- **UNVERIFIED:** none.


# Plan — Issue #784: Orders tab STATUS + PAIR COST columns

## Scope locked
- Stitch Orders table only (`stitchOpenOrdersHeadHtml` / `stitchOpenOrdersRows` in `dashboard/static/app.js`).
- Do NOT touch `core_brain/` (#795/#798), `dashboard/server.py` (backend/API), `dashboard/static/open-orders-preview.html`, classic Orders path, Active Markets EDGE, Positions defects (#785), category pill icons (#786).

## Grounded seams (verified verbatim)
- `stitchOpenOrdersHeadHtml(sort)` — `app.js:7852`; current cols: Market(1), CATEGORY(8), Dual-Leg Quotes(3), Total Committed(5), LAST UPDATED(0), Actions(null).
- `stitchOpenOrdersRows(kpi,state,sort)` — `app.js:7878`; sort switch cases 0,1,2,3,4,5,7,8 (no 6,9,10,11 in this view's switch).
- `pairStatus(upSize,dnSize)` — `app.js:7115` returns `paired`/`partial`/`unpaired`.
- `isHedgePending` — computed at `app.js:7977` (one leg filled, counterpart resting).
- `restingPairCost(orders,kpi)` — `app.js:7447` returns `null` when either resting leg absent.
- `fmtPrice(v)` — `app.js:7115`-ish; returns `--` for null/undefined.
- `OT_TEXT_COLUMNS['open-orders']` = `new Set([1,2,8])` (`app.js:6564`).
- Badge markup `app.js:8007-8028`; `stitch-meta-tag` at `app.js:8074`; `edge` var at `app.js:7921-7922` (only badge consumes it).
- CSS in `prototype.css`: `.stitch-arb-badge` (1037), `.is-paired` (1048 `#34d399`), `.is-unpaired` (1054 amber `#fcd34d`), `.is-partial` (1060), `.is-hedge-pending` (1066 `#7dd3fc`), `.badge-val` (1072), `.badge-edge` (1076), `.badge-sep` (1080), row tint `is-unpaired` (948-951), `.stitch-meta-tag` (1027).

## Key collision — stale CodeRabbit assumption
The CodeRabbit plan assumed 8/9 free. **#783 landed CATEGORY at key 8**, so key 8 is taken. New Orders keys: **STATUS=9, PAIR COST=10** (unused in Orders switch). Because the Stitch Orders switch is view-scoped, 9/10 don't collide with Active Markets' 9/10.

## Decisions (recorded, defaults stand)
1. `isPartial` → treated as **paired** (green `PAIRED`), hover title "Unequal leg sizes". Both orders on the book.
2. Remove redundant `stitch-meta-tag` line from Market cell — STATUS column now says it.
3. Pill labels `PAIRED` / `SINGLE FILL` / `MISSING PAIR`, word-only. No `Pair Cost:`, no `% Edge`.
4. PAIR COST shows `fmtPrice(pairCost)`; `--` when null.

## Sort keys (new)
- STATUS = **9** (text column → add to `OT_TEXT_COLUMNS['open-orders']`, first click A→Z).
- PAIR COST = **10** (numeric, not text; first click high→low, null last via `otSortGroups`).

## Implementation steps

### 1. Head (`stitchOpenOrdersHeadHtml`)
Final order: Market(1), CATEGORY(8), STATUS(9), Dual-Leg Quotes(3), PAIR COST(10), Total Committed(5), LAST UPDATED(0), Actions(null). Add STATUS after CATEGORY, PAIR COST after Dual-Leg Quotes.

### 2. Row (`stitchOpenOrdersRows`)
- Move status computation before sort (already computed? current status computed inside map). Keep `isHedgePending`, `pairStatus`, `restingPairLegs` logic; compute status per group in the sort `valueOf` via a Map, rendering reads same value.
- Sort `case 9` → status label; `case 10` → `restingPairCost(group.orders,kpi)` (null stays null). Add key 9 to `OT_TEXT_COLUMNS['open-orders']`.
- Replace badge spans: word-only pill `PAIRED` (is-paired), `SINGLE FILL` (is-hedge-pending), `MISSING PAIR` (is-unpaired) with `title` hover (escape with `esc`): partial→"Unequal leg sizes", unpaired+matchedHeld→"Partial Fill Held", else "No Position".
- Remove `badge-val`, `badge-edge`, `badge-sep` spans, `stitch-meta-tag`, and the `edge` variable (verify no other consumer — confirmed only badge).
- Add PAIR COST cell after quotes cell: `<td>${fmtPrice(pairCost)}</td>`.
- Empty-table colspan 6 → 8.

### 3. CSS (`prototype.css`)
- `.stitch-arb-badge.is-unpaired`: recolor amber `#fcd34d` → red `#f87171`, matching red bg/border.
- Add `white-space: nowrap` to `.stitch-arb-badge`.
- Remove `.badge-val`, `.badge-edge`, `.badge-sep`, `.is-partial` rules.

### 4. Tests
- `tests/test_stitch_open_orders.py`: head test 5→8 columns (with STATUS + PAIR COST + Actions unsortable); unpaired row expects `MISSING PAIR`; hedge-pending expects `SINGLE FILL`; empty colspan 8; add pair-cost row test.
- `tests/test_orders_trades_table.py`: add stitch-surface tests pinning labels+classes, no `% Edge`/`Pair Cost:` in pills, no `stitch-meta-tag`, key 9 asc sort, key 10 numeric sort with null last, source checks for `#f87171`/`#34d399`/`#7dd3fc` and absence of `#fcd34d` in unpaired rule.

## Verification
- `node --check dashboard/static/app.js`.
- `python -m pytest tests/test_orders_trades_table.py -q`.
- `python -m pytest -q tests/test_stitch_open_orders.py tests/test_stitch_active_markets.py`.
- Served-asset smoke: serve dashboard, curl `app.js`/`prototype.css`, grep new labels + absence of `% Edge`.

# CONSTRAINTS — #656

Branch: i656/active-trading-green-pills | Plan: `tasks/plan.md`

The bar this change is held to. Weakening any line to reach green is a failure,
not a fix.

## 1. Zero regressions
- Baseline on HEAD `01157a3`: 134 passed (`test_stitch_positions_marks.py`,
  `test_orders_trades_table.py`, `test_positions_live_marks.py`), 194 passed
  across the six focused suites, 2 passed / 128 deselected for the issue's
  acceptance command. All must still pass.
- Every new behaviour ships with a test that fails without the change. The only
  deliberate exception is the reduced-motion/`hb-blink` guard, which is a
  boundary tripwire and passes on both sides; it is labelled as such in
  `tasks/plan.md` §T3.

## 2. Anti-cheat
- No `skip`, `xfail`, or deleted/weakened assertions.
- No `# type: ignore`, `# noqa`, `eslint-disable`, or any other suppression.
- No edits to `tests/js/*.cjs` unless a fixture genuinely cannot reach the
  renderer; both harnesses already accept `kpi`/`state`/`view`.
- If a Node test skips because `node` is missing, that run proves nothing —
  `node --version` must succeed before a green result is reported.

## 3. Dependencies
- No new external dependency, no new tooling, no packaging change.

## 4. Frozen surfaces (any edit here fails the review)
- `dashboard/static/styles.css` — including `@keyframes hb-blink`
  (`styles.css:433`) and the reduced-motion block (`styles.css:599-603`).
- `dashboard/static/index.html`, `dashboard/static/prototype.js`,
  `dashboard/static/prototype.css`, `dashboard/server.py`,
  everything under `core_brain/`, `data/orders.db`.
- In `dashboard/static/app.js`: `marketStatusPill`, `isQuotedMarket`,
  `isRestingOrder`, `groupOrdersByMarket`, `heldMarketEntries`,
  `activeMarketsRows`, `stitchActiveMarketsRows`, `renderScreener`,
  `paintLiveMarks`.
- The two named acceptance tests.

## 5. DOM contract (hard boundary)
- No new table column in either positions table; the classic positions head and
  the stitch 8-column head are unchanged, as are row counts and band classes
  (`ot-pair-start`, `ot-pair-alt`).
- The pill carries no `data-cell`, no `data-cid`, and no `live` class, and no
  `data-cell` cell may contain a breathing class — `paintLiveMarks` owns those
  cells.
- Idle held markets emit no pill markup at all, and the IDLE (`pill stopped`)
  badge must never appear in either positions table.
- The pill appears on exactly the markets the ACTIVE MARKETS tab lists, with the
  same RESTING-versus-QUOTING verdict.

## 6. Performance
- One `groupOrdersByMarket(state && state.orders)` call per render, matching the
  calls `activeMarketsRows`, `ordersTradesCounts` and `renderOrdersTrades`
  already make. No per-row map rebuild inside a leg loop.

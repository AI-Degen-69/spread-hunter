# TODO — #656 active-trading breathing pills in Traded Positions

Branch: i656/active-trading-green-pills | Plan: `tasks/plan.md`

## T1 — shared rule + served Stitch Positions table  [Design/UI]
- [ ] `app.js:7292` add `marketActivityPill(m, orders)` after `marketStatusPill`
- [ ] `app.js:10498` export `marketActivityPill` beside `marketStatusPill`
- [ ] `app.js:8344-8348` `stitchPositionsRows`: `groupOrdersByMarket(state && state.orders)`
- [ ] `app.js:8381-8385` add `activity` to the per-market row object
- [ ] `app.js:8409-8413` add `activity` to the `sorted.map` destructure
- [ ] `app.js:8456-8457` insert the `.stitch-market-meta` pill div (unique multi-line anchor)
- [ ] `tests/test_stitch_positions_marks.py`: `_kpi(quotes_count=0)` param
- [ ] direct unit tests for the helper (idle / quoting / resting precedence)
- [ ] renderer tests: resting (open, partial, pending, Open), resting-wins, quoting-only, idle, other-market, finished
- [ ] extend the live-overlay test: pill survives marks, sits outside `data-cell`
- [ ] `python -m pytest -q tests/test_stitch_positions_marks.py` → RED then GREEN

## T2 — pill on the classic `positionsRows` table  [Design/UI]   (Depends on: T1)
- [ ] `app.js:7667-7668` `groupOrdersByMarket(state && state.orders)` once per render
- [ ] `app.js:7724-7726` `const activity = marketActivityPill(m, ordersByMarket[cid]);`
- [ ] `app.js:7735` `${marketCell(...)}${activity}${pairTags}`
- [ ] `tests/test_orders_trades_table.py`: quoting (baseline fixtures), resting (+open order), other-market isolation, idle, banding/live-cell guard with a second held market
- [ ] `python -m pytest -q tests/test_orders_trades_table.py tests/test_positions_live_marks.py` → GREEN

## T3 — source checks, guard, focused run  [Core]   (Depends on: T1, T2)
- [ ] `tests/test_dashboard_server.py`: `test_positions_renderers_wire_activity_pill`
- [ ] `tests/test_dashboard_server.py`: `test_styles_css_reduced_motion_covers_breathing_pills`
- [ ] six focused suites green (194 test baseline)
- [ ] new source-check `-k` selection green
- [ ] issue acceptance command green (2 passed, 128 deselected baseline)
- [ ] `node --version` present (harness tests must not skip)

## Boundaries
- [ ] no edits: `styles.css`, `index.html`, `prototype.js`, `prototype.css`, `dashboard/server.py`, `core_brain/**`, `data/orders.db`
- [ ] no new column, no `data-cell`/`data-cid`/`live` on the pill, no CSS or `hb-blink` change
- [ ] `CONSTRAINTS.md` holds

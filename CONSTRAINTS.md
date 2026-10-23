# CONSTRAINTS — Issue #783 (locked quality guardrails)

1. **Zero regressions:** the focused suites covering touched code must pass —
   `tests/test_orders_trades_table.py`, `tests/test_sidebar_prototype.py`,
   `tests/test_stitch_active_markets.py`, `tests/test_stitch_open_orders.py`,
   `tests/test_stitch_positions_marks.py`, `tests/test_stitch_closed_trades.py`.
   (The full `python -m pytest -q` suite stays the GitHub CI merge gate.)
2. **Every changed behaviour gets a test that fails without the change** (RED before GREEN:
   new/updated assertions must fail on the unmodified renderers first).
3. **Sort-key stability:** never renumber or reuse an existing `sortIdx`;
   classic Active Markets keys 5/6 are not touched; new key 8 is Orders-only
   and must be added to `OT_TEXT_COLUMNS['open-orders']` so its first click sorts ascending.
4. **Header legibility:** `.proto-body #orders-trades-table th` keeps `padding: 12px 16px`.
5. **No positional selectors:** no `nth-child` / `td:first-child` rules in CSS
   (an existing test bans `td:first-child`; merged rows use rowspan).
6. **Classic path untouched:** `otHeadHtml` / `ordersTradesRows` and the classic assertions
   (`test_orders_trades_table.py` classic cases, keys 0–7) keep passing unmodified.
7. **Pill uniqueness:** exactly one `stitch-category-pill` per stitch row, inside the new
   `stitch-category-cell`, never inside `stitch-market-cell`.
8. **No new dependencies, no backend/API change, no `data/*.db` writes;**
   hands-on checks use the demo database only and never press START / BACK TO LIVE.
9. **Anti-cheat:** no skipped/disabled tests, no deleted assertions to force green,
   no linter suppressions. A failing check is fixed at its cause or reported honestly.

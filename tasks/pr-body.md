## Why

The four Orders & Trades tabs each labelled the same market differently. The category
resolver walked a chain — venue label, then `series_title`, then `market_group`, then the
first tag — so one market could read `NBA Tip-Off` on the orders table, `Basketball` on
Active Markets and nothing at all on Open Positions, which hid the chip when the answer was
`Uncategorized`. The venue only publishes a reliable category as a **tag**, and gamma omits
tags unless the request asks for them, so the dashboard was mostly guessing from title
keywords.

The fix is one resolver and one chip:

- `core_brain/market_meta.py` ranks tags instead of taking the first one. A named sport or
  game beats a parent shelf (`Basketball` beats `Sports`), a parent shelf beats any other
  top-level shelf, and text the vocabulary does not name (`NBA Tip-Off`, `Match Winner`,
  `Republican Party`) is never a label. Precedence is now venue label → ranked tags →
  title classifier → `Uncategorized`; `series_title` and `market_group` are no longer
  category sources.
- `scripts/filter_markets.py` sends `include_tag=true` on both the `/markets` page request
  and the live-events request, and de-duplicates the market and event tag lists.
- `dashboard/static/app.js` renders `categoryPill(m)` in all four views — Open Orders,
  Active Markets, Open Positions, Closed Trades. `Uncategorized` renders as a dimmed chip
  rather than a missing one, so every row keeps the same column shape. Open Positions now
  puts the event name on the second line instead of repeating the category.

Nothing here touches order sizing, pricing or pair assembly, so the two failure modes this
repo guards against — a pair over $1.00 and a single unmatched buy — are unaffected.

## Test output

```
python -m pytest -q tests/test_market_meta.py tests/test_orders_trades_table.py tests/test_stitch_active_markets.py tests/test_kpi.py tests/test_unified_universe.py tests/test_live_event_discovery.py tests/test_market_feed.py tests/test_sidebar_prototype.py
403 passed, 2 skipped in 14.35s

python -m pytest -q tests/test_portfolio_overview.py
31 passed in 2.82s

python -m pytest -q tests/test_stitch_closed_trades.py tests/test_stitch_open_orders.py tests/test_stitch_positions_marks.py
41 passed in 2.42s

python -m pytest -q tests/test_dashboard_snapshot_cache.py tests/test_dashboard_narrow_viewport.py
14 passed in 0.88s
```

GitHub CI runs the full `python -m pytest -q` regression suite on Ubuntu and Windows; local
full-suite output is not required at the review/PR stations.

## How to verify

1. `.\scripts\spread-hunter-menu.ps1 start`, then open `http://127.0.0.1:8799`.
2. Go to **Orders & Trades** and click through **Open Orders**, **Active Markets**,
   **Open Positions** and **Closed Trades**.
3. Read the chip under a market name on each tab. The same market must show the same word
   on all four tabs — for example `Basketball`, not `NBA Tip-Off` on one and `Sports` on
   another.
4. Check a row the venue has no category for: the chip must read `Uncategorized` in a
   dimmed style, and the row must keep the same height as its neighbours instead of
   collapsing.
5. On **Open Positions**, the line under the chip is the event or market name. It must
   never repeat the category word.

Closes #748

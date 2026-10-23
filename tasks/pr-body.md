## Why

The Orders table's Market cell held a long arbitrage badge that mixed the pair's state,
its cost, and its edge in one crowded pill. This splits that into two dedicated,
sortable columns: a word-only STATUS pill and a PAIR COST figure. It also removes the
redundant `% Edge` text and the meta tag, which the new STATUS column now says.

## Decisions (recorded once, in the PR body)

1. **`isPartial` is treated as paired (green).** Both orders are on the book; the
   only difference is unequal remaining sizes, surfaced as hover text "Unequal leg sizes".
2. **The redundant `stitch-meta-tag` line is removed from the Market cell.** STATUS now says it.
3. **Pill labels are word-only:** `PAIRED` / `SINGLE FILL` / `MISSING PAIR`.
   No `Pair Cost:`, no `% Edge` inside the pill.
4. **PAIR COST shows `fmtPrice(pairCost)`**, and `--` when the cost is unknown.

## New sort keys

- STATUS = **9** (text column; first click sorts A→Z).
- PAIR COST = **10** (money column; first click sorts high→low, `--` last).

## Test output

```
$ node --check dashboard/static/app.js
SYNTAX OK

$ python -m pytest tests/test_orders_trades_table.py tests/test_stitch_open_orders.py -q
176 passed in 15.34s

$ python -m pytest -q tests/test_stitch_active_markets.py
3 passed in 1.20s
```

Served-asset smoke (server on 127.0.0.1:8800): `app.js` contains the new labels,
no old badge/edge text; `prototype.css` unpaired rule is `#f87171` with no amber; the
`is-partial` badge rule is gone.

## How to verify

1. Start the dashboard with `python -m dashboard.server --db data/demo_orders.db --host 127.0.0.1 --port 8799`, then open `http://127.0.0.1:8799`.
2. Go to Dashboard → ORDERS & TRADES → Orders (select Orders again after a reload, since the page remembers the last tab). Do not press START.
3. Expected: a STATUS column and a PAIR COST column appear. Each pill shows only its label — green `PAIRED`, blue `SINGLE FILL`, or red `MISSING PAIR`. PAIR COST shows one price or `--`. If the data has no row in a given state, that colour won't appear; that is fine.
4. Click the STATUS header once: `MISSING PAIR` rows move to the top. Click PAIR COST: the highest cost is first and `--` rows stay at the bottom.
5. Failure examples: the old long badge in the Market cell, an amber missing-pair pill, "Edge" text, or "Pair Cost:" text inside a pill.

Closes #784

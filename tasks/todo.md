# Todo — Issue #831 (Closed Trades: wins/losses + exec-type pills)

Branch: `i831/closed-trades-wins-losses-exec-pills`
Derived checklist — `tasks/plan.md` is the source of truth.

- [x] T1 — `stitchSettlementPnl(s)` helper next to `closedTradeCostBasis`; ledger rows call
      it (behaviour unchanged). `dashboard/static/app.js`.
- [x] T2 — Outcome Breakdown counts `winCount` / `lossCount`; green `N Wins`, red
      `N Losses`, neutral `N Closed` fallback; `.stitch-pill-count.win` / `.loss` CSS,
      method modifiers retired, and the duplicated summary strip deleted with its dead CSS
      (it read the deleted counters and could not outlive them). `app.js`, `prototype.css`.
- [x] T3 — Exec-type pills (`is-merge`/`is-sale`/`is-stop-loss`/`is-grace-expired`/
      `is-resolved`), `Pair Merge`→`Merge`, `Settlement`→`Resolved`. `app.js`,
      `prototype.css`.
- [x] T4 — Updated the pair test; added mixed / explicit-zero / missing-value /
      all-labels / synthetic-fallback / strip-removed tests. All six new tests fail on the
      pre-change `app.js`. `tests/test_stitch_closed_trades.py`.
- [x] Focused checks: `tests/test_stitch_closed_trades.py`, `tests/test_orders_trades_table.py`,
      `tests/test_close_method_taxonomy.py`, `tests/test_stitch_open_orders.py`,
      `tests/test_stitch_active_markets.py` → 226 passed.
- [ ] Operator click path (Final check): dashboard → Closed Trades → Wins/Losses pills,
      View Ledger → exec-type pills, no summary strip, parent totals unchanged.

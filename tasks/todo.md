# Todo — Issue #831 (Closed Trades: wins/losses + exec-type pills)

Branch: `i831/closed-trades-wins-losses-exec-pills`

- [ ] T1 — `stitchSettlementPnl(s)` helper next to `closedTradeCostBasis`; ledger rows call
      it (behaviour unchanged). `dashboard/static/app.js`.
- [ ] T2 — Outcome Breakdown counts `winCount` / `lossCount`; green `N Wins`, red
      `N Losses`, neutral `N Closed` fallback; `.stitch-pill-count.win` / `.loss` CSS and
      retire the three method modifiers. `app.js`, `prototype.css`.
- [ ] T3 — Exec-type pills (`is-merge`/`is-sale`/`is-stop-loss`/`is-grace-expired`/
      `is-resolved`), `Pair Merge`→`Merge`, `Settlement`→`Resolved`, delete the
      `stitch-closed-ledger-strip` block and its dead CSS. `app.js`, `prototype.css`.
- [ ] T4 — Update the pair test; add mixed / explicit-zero / missing-value /
      all-labels / synthetic-fallback / strip-removed tests.
      `tests/test_stitch_closed_trades.py`.
- [ ] Run focused checks: `tests/test_stitch_closed_trades.py`,
      `tests/test_orders_trades_table.py`, `tests/test_close_method_taxonomy.py`.
- [ ] Operator click path (How to verify): dashboard → Closed Trades → Wins/Losses pills,
      View Ledger → exec-type pills, no summary strip, parent totals unchanged.

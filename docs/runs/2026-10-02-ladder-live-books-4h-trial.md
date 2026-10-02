# Full 4-hour Live-Books Ladder Trial

**Run Date:** 2026-10-02
**Report File:** `reports/ladder_live_books_333_20261002_0615.json`
**Issue:** #333

## Scope & Objective
This report details the results of the ~4-hour live-books shadow trial for the ladder strategy over BTC/ETH 5-min markets. As discovered during the trial and noted in the plan, **this report focuses exclusively on placement and censoring.**

> [!WARNING]
> **Fills were ~0, and the fill/exit half of the probe comparison was not measured.**
> The probe baseline (75% pair rate, shape 2 / exit_60, +0.30 CI 0.26–0.35) is **not comparable** to this run's placement data. Pair rate, exit_60 vs hold-to-close, PnL, and the shape-2 verdict impact are all out of reach for this run because there were no fills to evaluate.

## Placements, Cancels, and Queue Depth
- **Volume and Rate:** The trial placed 223 orders (114 UP, 109 DOWN) across 96 markets over ~240 minutes, which corresponds to roughly 55 orders/hour.
- **Cancel Reasons & Rest Time:** Based on live diagnostics, orders were predominantly cancelled as `not_quoted` because the ladder is live for only 30 seconds of every 300-second market (`--open-window-sec 30`), leading to a mean rest time of approximately 17.0s per order.
- **Queue Ahead Depth:** Placements rested behind significant queue depth (mean ~394 shares, with a max of 1332). For a fill to occur, the tape volume at the exact price would need to consume that entire queue first within the ~17s window, explaining why fills were effectively zero.

## Conservation Result
Conservation holds with zero orphans.

| Metric | Shares/Count |
| --- | --- |
| Orphan Fills | 0 |
| Overfilled Orders | 0 |
| Double Counted Fills | 0 |
| Total Fills (shares) | 0 |
| Accounted Shares | 0.0 |
| Resting Shares | 0.0 |

All created legs either cancelled normally, expired, or failed to fill, resulting in a completely flat book with 0 fills and no outstanding or double-counted shares.

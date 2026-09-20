# Audit: dollar expectancy vs mean return (Issue #248)

Screenshot case: **+$0.11** average close / expectancy alongside **−0.70%**
Mean Return Per Trade. Both numbers are correct for their names — they average
different things over different populations.

## Metric table

| Metric | Numerator | Denominator | Population | Weighting | Sign interpretation |
|---|---|---|---|---|---|
| `expectancy_usd` ("Average Profit Per Close") | Σ `realized_pnl` | n = ALL closes | every close, including ones with missing/non-positive `cost_basis` | equal-weighted **dollars** per close | Positive when the average close earns dollars — large-dollar wins count most. |
| `mean_return_pct` ("Mean Return Per Trade") | Σ (100·`realized_pnl`/`cost_basis`) | m = closes with valid positive `cost_basis` only (m ≤ n) | measured closes only; unmeasured ones are excluded, never zero-filled | equal-weighted **percents** per close | Positive when the average measured trade earns percent — a small-cost −100% trade counts as much as a large +30% win. |
| `dollar_weighted_return_pct` (companion) | 100·Σ `realized_pnl` | Σ `cost_basis` | measured closes only (same m as the percent mean) | **dollar-weighted** percent | The percent version of the dollar story — always agrees in sign with `expectancy_usd` over the measured population. Positive when measured dollars are net positive. |

## Why the signs can differ

A small-cost trade with a large percent loss (e.g. −$1 on $1 = −100%)
dominates the equal-weighted percent mean while barely moving the dollar mean.
Larger-dollar wins (e.g. +$1.50 on $5 = +30%) keep the dollar expectancy
positive at the same time. Neither number is wrong; they answer different
questions:

- **Dollars:** "does the average close earn money?" (`expectancy_usd`)
- **Percents:** "does the average measured trade earn percent?" (`mean_return_pct`)
- **Bridge:** "does the measured capital earn percent?" (`dollar_weighted_return_pct`)
- **Coverage:** "how many closes does the percent number actually see?" (`n_measured_returns` vs `n_closes`)

## Verdict

Formulas are correct for their names and **unchanged by this audit**.
The fix is display-only: companion fields plus plain-language copy so the
operator sees the distinction. The GO/NO-GO gate
(`passed` = `ci90_lower_pct` gate AND dollar-twin gate) reads none of the new
fields.

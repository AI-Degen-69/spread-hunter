# SPEC — #448: Redesign sample size sufficiency using effect size and per-status observation bases

Scope note: this file covers issue #448 only
(branch `i448/redesign-sample-size-sufficiency`).
It supersedes the #443 spec.

## Problem (operator words)

The current sample size sufficiency card on the Reports & Analytics dashboard requires 30,000 to 52,000 observations because it calculates sample size using a fixed absolute margin $E = \$0.02$ against high dollar standard deviation ($\sigma \approx 1.77$). In a live market setting, sample size sufficiency must be practical (200–400 observations) and categorized per metric status (closed trades vs placed orders) rather than collapsing all metrics into a single arbitrary dollar threshold.

## Goals

1. **Continuous metrics (Cohen's d = 0.20):**
   Implement relative effect size sample size calculation:
   $$N = \left(\frac{Z}{d}\right)^2$$
   For continuous metrics:
   - PnL Expectancy (based on `closes` table)
   - Holding Duration (based on `closes` table)
   With $Z_{95\%} \approx 1.95996$ and $d = 0.20$, $N \approx \lceil(1.95996 / 0.20)^2\rceil = 97$ (or ~97–100 observations).

2. **Proportion-based status metrics ($E_{pct} = 0.05$):**
   Implement proportion-based sample size calculation:
   $$N = \left\lceil \frac{Z^2 \cdot p(1-p)}{E_{pct}^2} \right\rceil$$
   With $Z_{95\%} = 1.95996$, conservative $p = 0.50$ (or measured rate when available), $E_{pct} = 0.05$:
   $N \approx \lceil (1.95996^2 \cdot 0.25) / 0.0025 \rceil = 385$ observations.
   Categorized across respective observation bases:
   - Stop Loss Exit Rate (`closes` table, exits via stop loss / single buy)
   - Merge Rate (`closes` table, exits via merge / shadow merge)
   - Fill Rate (`orders` table, total orders placed vs fills received)

3. **Domain Separation of Observation Counts:**
   - `closes` table as observation base for PnL Expectancy, Holding Duration, Stop Loss exits, and Merge exits.
   - `orders` table as observation base for Fill Rate.

4. **Surface on Dashboard UI:**
   Update the Reports & Analytics dashboard tab (`#card-sample-sufficiency` / `#sample-sufficiency-readout`) to surface segmented sufficiency metrics per status (PnL Expectancy, Stop Loss Rate, Merge Rate, Fill Rate) with their respective observation counts, targets, and progress bars.

5. **Version synchronization:**
   Bump `KPI_PAYLOAD_VERSION` in `core_brain/kpi.py` and `EXPECTED_PAYLOAD_VERSION` in `dashboard/static/app.js` to 255.

## Acceptance Criteria

- [ ] Continuous sample size formula $N = \lceil (Z / d)^2 \rceil$ evaluates to ~97–100 observations for $d = 0.20$ at 95% CL.
- [ ] Proportional sample size formula $N = \lceil (Z^2 \cdot p(1-p)) / E^2 \rceil$ evaluates to ~385 observations for $E = 0.05$ (at $p = 0.5$).
- [ ] `sample_size_sufficiency` payload in `/api/kpi` separates status targets:
  - `pnl_expectancy` (continuous, base: closes)
  - `stop_loss_rate` (proportional, base: closes)
  - `merge_rate` (proportional, base: closes)
  - `fill_rate` (proportional, base: orders)
- [ ] Dashboard displays individual sufficiency metrics per status on the Reports & Analytics tab.
- [ ] `tests/test_statistical_analytics.py`, `tests/test_mean_pnl_ci.py`, `tests/test_kpi.py`, `tests/test_analytics_api.py`, `tests/test_analytics_surface_mount.py` pass with 0 failures.

## Explicit Out of Scope

- Modifying live trading execution parameters or live order pricing logic.
- Adding external dependencies.

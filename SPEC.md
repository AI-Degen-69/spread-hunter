# SPEC — #443: Sample size sufficiency per confidence level on dashboard

Scope note: this file covers issue #443 only
(branch `i443/sample-size-sufficiency-per-confidence-level`).
It supersedes the #427 spec (done work).

## Problem (operator words)

Strategy analytics displays PnL expectancy and confidence intervals, but the dashboard never surfaces how many closed trades are needed to reach statistical confidence at each confidence level (95%, 98%, 99%). Operators cannot tell whether the expectancy readout rests on enough observations or how many more closed trades are needed.

## Goals

1. Pure calculation helper `required_sample_size_for_mean(std_dev, target_margin, z)` computes `ceil(((z * std_dev) / target_margin) ** 2)`. Returns 0 for non-positive or non-finite inputs.
2. Extend `/api/kpi` (`trade_analytics.sample_size_sufficiency`) with current N, sample standard deviation, target margin of error ($0.02 USD per close default), and a 3-row evaluation for 95%, 98%, and 99% two-tailed confidence.
3. Add a "Sample Size & Confidence Level Sufficiency" card to the Tier 1 decision row in the dashboard (`dashboard/static/index.html`, `app.js`, `styles.css`) with progress bars, showing "unmeasured" and clean guidance when N < 2 or when sample spread is 0.
4. Version synchronization: bump `KPI_PAYLOAD_VERSION` and `EXPECTED_PAYLOAD_VERSION` from 253 to 254.

## Acceptance Criteria

- [ ] Helper `required_sample_size_for_mean(std_dev, target_margin, z)` implements `ceil(((z * std) / E)^2)`, returns 0 for non-positive/non-finite inputs, and matches Z-values (1.95996, 2.32635, 2.57583) at E = 0.02.
- [ ] `/api/kpi` payload carries `trade_analytics.sample_size_sufficiency` with `current_n`, `std_dev_usd`, `target_margin_usd`, and `levels` (95, 98, 99) with `confidence_pct`, `z`, `required_n`, `remaining_n`, `progress_pct`.
- [ ] When N < 2 or spread is zero, `std_dev_usd` and row calculated metrics are `null`, avoiding false sufficiency.
- [ ] Dashboard card inside Tier 1 row (`#tier1-decision-row`) renders rows, progress bars, and informative text for N < 2 or zero spread without `undefined` or `NaN`.
- [ ] `KPI_PAYLOAD_VERSION` and `EXPECTED_PAYLOAD_VERSION` bumped to 254.
- [ ] Tests pass in `tests/test_kpi.py`, `tests/test_analytics_api.py`, `tests/test_analytics_surface_mount.py`, `tests/test_analytics_impact_tiers.py`, and `tests/test_negative_values_read_as_losses.py`.

## Explicit Out of Scope

- Changing gating or live quoting decisions.
- Adding new HTTP routes or endpoints.
- Modifying `evaluate_stat_gate`, `power_table`, or existing 90% CI gate logic.
- Adding new configuration settings to `core_brain/config.py`.
- Restyling other cards or modifying other tiers.

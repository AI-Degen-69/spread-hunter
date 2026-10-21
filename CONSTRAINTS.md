# Constraints — #749 Performance & Analytics consolidation

Branch: `i749/consolidate-reports-and-analytics` | Issue: #749

## Hard scope

- Dashboard presentation only. No change to `core_brain/**`, `scoring/**`,
  `dashboard/server.py`, `scripts/filter_markets.py`, the trading loop, the
  registry, or any venue path. A change that reaches one of those is out of
  scope — stop and split it.
- No new statistic, no new KPI payload field, no `KPI_PAYLOAD_VERSION` bump.
  Every rendered figure already exists in the payload the two cards read.
- `#analytics-gates` and `#broker-portfolio-overview` keep exactly one home in
  the document: deleting either breaks the Strategy or Home page.
- The ported cards keep their data visuals (CI band, 90 %/95 % rows, loss
  banner; four funnel stages, carry-on/lost arrows, WORST DROP, MERGED,
  refused/dropped chips). Only their card chrome is harmonised.

## Zero regressions

- Green on this branch: `tests/test_sidebar_prototype.py`,
  `tests/test_541_parity_ledger.py`, `tests/test_stop_slippage.py`,
  `tests/test_analytics_surface_mount.py`, plus `tests/test_541_data_analysis.py`,
  `tests/test_analysis_v2_binding.py`, `tests/test_analytics_api.py`,
  `tests/test_analytics_impact_tiers.py`, `tests/test_portfolio_card_basis.py`.
- New behaviour needs a test that fails without the change: the placement
  assertions for the ported hosts (`pnl-ci-readout`, `execution-funnel`,
  `stop-slippage-readout` inside `#analysis-v2`) are written first and RED.

## Anti-cheat

- No skipped, deleted or weakened test unless the subject of that test is the
  thing this issue deletes (the analytics sub-nav and the legacy-only decks).
  When a test is retired, its replacement or its reason is named in
  `tasks/plan.md`. Never delete an assertion to reach green.
- No `# type: ignore`, no bare `except: pass`, no lint suppressions.

## Dependencies

- No new external dependency, no new stylesheet, no new script file.

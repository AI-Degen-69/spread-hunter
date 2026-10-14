# Todo — #517 capital risk analytics for expectancy, drawdown and downside risk

Branch: `i517/add-capital-risk-analytics-for-expectancy-drawdown` | Issue: #517
Plan: `tasks/plan.md` · Guardrails: `CONSTRAINTS.md` · Spec: `SPEC.md`

- [x] T1 `[Backend/Logic]` M: `drawdown_episodes()` + `drawdown_recovery()` in
      `core_brain/statistical_analytics.py` (`starting_value`, `_finite`-skipped points counted in
      `skipped`, `v >= peak` regains, open episodes excluded from the medians, `_percentile` for
      median/p90, `drawdown_recovery_sec` omitted without timestamps); new
      `tests/test_capital_risk_analytics.py` with the recovery, no-recovery, rising-only, empty,
      zero-peak and bad-point cases plus the `kpi.compute_trade_analytics` oracle
- [x] T2 `[Backend/Logic]` M: `pnl_skewness()`, `skewness_band()`, `sortino_ratio()`, `sortino()`,
      `sortino_band()` and the settled band tables/words; the skewness, Sortino and band-edge vectors;
      the `kpi` Sortino oracle at `risk_free=0.0`; the reuse pins (`kpi._mean_pnl_ci is
      sa.mean_pnl_ci`, one `system_quality_number`) and the "no CI/SQN formula in this file" scan
- [x] T3 `[Backend/Logic]` S/M: `capital_risk()` and `build(..., equity_curve=None)` in
      `statistical_analytics.py`; `kpi.py`'s three edits (`KPI_PAYLOAD_VERSION = 257`, hoisted
      `account_series`, `equity_curve=account_series`); `EXPECTED_PAYLOAD_VERSION = 257` in
      `dashboard/static/app.js`; additions to the two `build()` cases in
      `tests/test_statistical_analytics.py`; one new demo-store pin in `tests/test_build_demo_db.py`
- [x] T4 `[Design/UI]` M: the `#capital-risk-readout` card in `dashboard/static/index.html`,
      `renderCapitalRisk` + its call in `renderAnalyticsSurface` + the `module.exports` entry in
      `dashboard/static/app.js`, the harness's stub id / direct call / printed HTML in
      `tests/js/analytics_surface_harness.cjs`, and the new mount cases in
      `tests/test_analytics_surface_mount.py` (two chips, four unbanded tiles, absent-section
      back-compat, band-follows-payload)
- [x] **CHKPT A** after T1+T2 — pure layer green in the focused file, RED captured for each
- [x] **CHKPT B** after T4 — payload carries `capital_risk`, card renders on the demo store, every
      focused suite in `CONSTRAINTS.md` green
- [x] The operator's hands-on "How to verify" walkthrough (demo store → dashboard → the new card)

## Notes carried into Station IV

- `tests/test_analytics_api.py`'s payload-version literal moved `256` → `257` with #517 appended to
  its bump chain. That is where this repo records a payload field shipping; the pin itself is not
  relaxed (added to `CONSTRAINTS.md`'s target files).
- The local shell exports `PORT=0`, and `tests/test_account_kpi.py` imports the server module at
  collection, so the focused suites were run with `unset PORT`. Environmental, not this branch.
- `docs/issues/517-noticed-but-not-touching.md` carries one `open` candidate (a paint-schedule race
  that can leave all five analytics readouts blank) — recorded, not touched.

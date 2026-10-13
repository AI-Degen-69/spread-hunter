# Todo — #516 post-fill markout and statistical robustness analytics

Branch: `i516/add-post-fill-markout-and-statistical-robustness` | Issue: #516
Plan: `tasks/plan.md` · Guardrails: `CONSTRAINTS.md` · Spec: `SPEC.md`

- [x] T1 `[Backend/Logic]` S: move the CI helper into `core_brain/statistical_analytics.py`
      (`CI_Z_BY_LEVEL`, `CI_T_BY_DF`, `ci_critical_value`, `mean_pnl_ci`); `kpi.py` imports
      `mean_pnl_ci as _mean_pnl_ci` and drops its four definitions; new
      `tests/test_markout_robustness_analytics.py` with the CI cases, the identity pin and the
      subprocess import-side-effect check
- [x] T2 `[Backend/Logic]` M: shared per-horizon loop with a per-row direction; `markout()`
      output pinned unchanged; `MARKOUT_GOOD_AT_OR_ABOVE_CENTS`, `MARKOUT_AVERAGE_ABOVE_CENTS`,
      `MARKOUT_BAND_WORDS`, `markout_cents_band()`, `signed_markout()` (median · cents · signed)
- [x] CHKPT A: helper in one place, `tests/test_mean_pnl_ci.py` + `tests/test_trade_analytics.py`
      green unchanged; `git status --short` limited to the planned files
- [x] T3 `[Backend/Logic]` M: `HeldMark`, `MARKOUT_HORIZON_SECONDS`,
      `max_adverse_excursion()`, `excursion_summary()` (window bounded by the close; every row in
      exactly one bucket), `SQN_NOISE_BELOW`, `SQN_STABLE_EDGE_ABOVE`, `sqn_band()`,
      `SQN_BAND_WORDS`, `system_quality_number()`; horizon-seconds pin against
      `core_brain.config.load().markout_horizons`
- [x] CHKPT B: the ticket's focused file green with the RED output recorded per task;
      `tests/test_statistical_analytics.py` untouched and green so far
- [x] T4 `[Backend/Logic]` + `[Design/UI]` L: `robustness()` always-present section + the
      additive `build()` key; `#robustness-readout` card, `renderRobustness` and its call in
      `renderAnalyticsSurface`, `module.exports`; harness stub + printed HTML; mount cases
      (6 chips, MAE unbanded, no-section payload renders none); demo-store seeding +
      `tests/test_build_demo_db.py` pin; `tests/test_statistical_analytics.py` additions only
- [x] CHKPT C: focused suites green (`tests/test_markout_robustness_analytics.py`,
      `tests/test_statistical_analytics.py`, `tests/test_mean_pnl_ci.py`,
      `tests/test_trade_analytics.py`, `tests/test_analytics_surface_mount.py`,
      `tests/test_build_demo_db.py`, `tests/test_queue_realism_analytics.py`, `tests/test_kpi.py`);
      the card read back from the running dashboard on the demo store
- [ ] Handoff to `/iv-review-build-and-pr`

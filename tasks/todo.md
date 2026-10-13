# Todo — #514 rescue friction analytics (SLED, hops, TCDR, overshoot)

Branch: `i514/add-rescue-friction-analytics-for-stop-loss-drag` | Issue: #514
Plan: `tasks/plan.md` · Spec: `SPEC.md` · Guardrails: `CONSTRAINTS.md`

- [ ] T1: `core_brain/statistical_analytics.py` — `STOP_OFFSET_FALLBACK`, `_LOSS_EPSILON`,
      `_entry_price`, `_exit_price`, `_stop_reference`, `rescue_drag()`; new
      `tests/test_rescue_friction_analytics.py` with fixtures A–E
      (RED first: import error; exact additivity for B; unclipped signs for C; `assumed_offset`
      tag for D; `no_planned_risk` for E)
- [ ] T2: same files — `taker_cost_drag_ratio()`, `overshoot_ratio()`, `FRICTION_BAND_METRICS`,
      `friction_band()`, `rescue_friction_summary()`; band-boundary, zero-division,
      empty-dataset, non-rescue, `CloseRecord`, strict-JSON and no-mutation tests
- [ ] CHKPT 1: every metric measured; focused suite green; nothing outside the two files touched
- [ ] T3: `build()` gains the always-present `rescue_friction` key; extend the populated and
      empty `build()` tests in `tests/test_statistical_analytics.py`
- [ ] T4: `#rescue-friction-readout` card (`index.html`), `renderRescueFriction` (`app.js` +
      `module.exports`), harness call (`tests/js/analytics_surface_harness.cjs`), mount cases
      (value + band chip, `unmeasured` with no `0.00`/`NaN`)
- [ ] CHKPT 2: focused suites green (`test_rescue_friction_analytics.py`,
      `test_statistical_analytics.py`, `test_fill_path_analytics.py`,
      `test_analytics_surface_mount.py`, `test_dashboard_server.py`)
- [ ] Handoff to `/iii-build-plan auto`

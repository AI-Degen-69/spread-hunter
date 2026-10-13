# Todo — #514 rescue friction analytics (SLED, hops, TCDR, overshoot)

Branch: `i514/add-rescue-friction-analytics-for-stop-loss-drag` | Issue: #514
Plan: `tasks/plan.md` · Spec: `SPEC.md` · Guardrails: `CONSTRAINTS.md`

- [x] T1: `core_brain/statistical_analytics.py` — `STOP_OFFSET_FALLBACK`, `_LOSS_EPSILON`,
      `_entry_price`, `_exit_price`, `_stop_reference`, `rescue_drag()`; new
      `tests/test_rescue_friction_analytics.py` with fixtures A–E
- [x] T2: same files — `taker_cost_drag_ratio()`, `overshoot_ratio()`, `FRICTION_BAND_METRICS`,
      `friction_band()`, `rescue_friction_summary()`; band-boundary, zero-division,
      empty-dataset, non-rescue, `CloseRecord`, strict-JSON and no-mutation tests
- [x] CHKPT 1: every metric measured; focused suite green; nothing outside the two files touched
- [x] T3: `build()` gains the always-present `rescue_friction` key; extended the populated and
      empty `build()` tests in `tests/test_statistical_analytics.py`
- [x] T4: `#rescue-friction-readout` card (`index.html`), `renderRescueFriction` (`app.js` +
      `module.exports`), harness call (`tests/js/analytics_surface_harness.cjs`), mount cases
- [x] CHKPT 2: focused suites green; the card verified live against a seeded store on
      `http://127.0.0.1:8801`
- [ ] Handoff to `/iv-review-build-and-pr`

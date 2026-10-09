# Tasks Checklist: Issue #443

- [x] Task 1: [Backend/Logic] Formula helper, KPI payload extension, and version bump
  - [x] Add Z constants and `required_sample_size_for_mean` to `core_brain/kpi.py`
  - [x] Add `sample_size_sufficiency` to `compute_trade_analytics` in `core_brain/kpi.py`
  - [x] Bump `KPI_PAYLOAD_VERSION` to 254
  - [x] Update version pins in `tests/test_analytics_api.py` and `tests/test_negative_values_read_as_losses.py`
  - [x] Create `tests/test_kpi.py` with formula and payload tests
  - [x] Verify Task 1 tests pass

- [x] Task 2: [Design/UI] Frontend card, CSS styling, and client-side render logic
  - [x] Add `#card-sample-sufficiency` inside `#tier1-decision-row` in `dashboard/static/index.html`
  - [x] Add `"card-sample-sufficiency": "1"` in `tests/test_analytics_impact_tiers.py`
  - [x] Add CSS styling in `dashboard/static/styles.css`
  - [x] Bump `EXPECTED_PAYLOAD_VERSION` to 254 in `dashboard/static/app.js`
  - [x] Implement `renderSampleSufficiency(ta)` in `dashboard/static/app.js`
  - [x] Call `renderSampleSufficiency(ta)` in `renderAnalyticsSurface`
  - [x] Export `renderSampleSufficiency` in `module.exports`
  - [x] Verify Task 2 tests pass

- [x] Task 3: [Backend/Logic] JS Harness integration, mount test suite & end-to-end verification
  - [x] Update `tests/js/analytics_surface_harness.cjs` with new element IDs and render call
  - [x] Add mount tests in `tests/test_analytics_surface_mount.py`
  - [x] Run focused test suites to verify zero regressions

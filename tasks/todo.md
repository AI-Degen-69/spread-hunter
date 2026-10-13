# Todo — #513 fill-path edge metrics (merge vs rescue)

Branch: `i513/add-fill-path-edge-metrics-for-merge-vs-rescue`

- [ ] T1: `core_brain/statistical_analytics.py` — `RESCUE_METHODS`, `EXCLUDED_METHODS`,
      `fill_path_edge()`, `break_even_rescue_rate()`; new `tests/test_fill_path_analytics.py`
      (RED first: import error)
- [ ] T2: `build()` gains the `fill_path_edge` key; extend populated/empty `build()` tests in
      `tests/test_statistical_analytics.py` (RED first: `KeyError`)
- [ ] T3: `#fill-path-edge-readout` card (`index.html`), `renderFillPathEdge` (`app.js` +
      `module.exports`), harness call, mount-test cases
- [ ] Focused suites green (nothing else in the tree touched)
- [ ] Handoff to `/iii-build-plan auto`

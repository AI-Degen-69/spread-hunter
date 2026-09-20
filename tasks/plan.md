# Issue #242 — Portfolio equity chart and header cleanup

## Scope and classification
- **Size:** Standard — coordinated HTML/CSS/JavaScript changes plus focused dashboard test coverage, with no backend or schema change.
- **Types:** Design/UI + Code.
- **Stack:** Browser JavaScript served by the Python dashboard, Python/pytest test suite, and an existing Node-based portfolio harness.
- **Primary skills:** `frontend-ui-engineering`, `test-driven-development`, `incremental-implementation`, and `verification-before-completion`.

## Evidence-based improvement proposal
Extract the chart-point construction into a small deterministic helper inside `dashboard/static/app.js` and test its output directly, because the issue notes that the existing harness skips the chart when its containers are absent; this keeps the new real-data behavior testable without adding a browser dependency.

## Implementation tasks

### Task 1 — Add the chart test seam [Code/Test]
- **Files:** `tests/js/portfolio_card_harness.cjs`, `tests/test_portfolio_card_basis.py` (or the nearest existing portfolio-card test location).
- **Build:** Extend the existing harness with non-null chart SVG and tooltip stubs, and add fixtures/assertions for close-series input, empty input, and a final current value.
- **Verification:** Run the focused portfolio-card test; first confirm the new assertion fails against the current synthetic chart path, then keep the failing test as the RED proof before implementation.

### Task 2 — Extract and build real equity points [Code/Frontend]
- **Files:** `dashboard/static/app.js`.
- **Build:** Add the deterministic chart-series helper proposed above. Filter to `type === "close"`, preserve chronological order, prepend the starting-capital anchor, append the current total value, and remove the `Math.pow(prog, 0.9)` synthetic fallback.
- **Verification:** Run the focused portfolio-card test and assert the plotted values represent the known close points plus the final current value.

### Task 3 — Implement real time labels and baseline [Design/UI]
- **Files:** `dashboard/static/app.js`.
- **Build:** Convert epoch-second close timestamps with `new Date(ts * 1000)`, retain the four-tick layout, label the final point `Current`, and make the START baseline full-width and dotted.
- **Verification:** Focused chart assertions must check real timestamp-derived labels, `Current`, and the baseline `stroke-dasharray`; inspect the rendered chart in a browser preview if the harness cannot expose the final SVG attributes.

### Task 4 — Handle empty state and preserve interaction [Design/UI]
- **Files:** `dashboard/static/app.js`, `tests/test_portfolio_card_basis.py`.
- **Build:** Render a flat starting-capital baseline with zero close entries; preserve mousemove, mouseleave, crosshair, and tooltip behavior. Restrict tooltip rows to real point fields (`v`, `pnl`, and optional `market`).
- **Verification:** Focused test covers zero closes and tooltip field presence; browser verification confirms no console errors and a usable empty chart.

### Task 5 — Simplify the Portfolio Overview header [Design/UI]
- **Files:** `dashboard/static/index.html`, `dashboard/static/styles.css`.
- **Build:** Move Starting Bankroll into `.broker-title-group`; remove the venue badge and Settlement Currency line; retain the venue wallet row and all seven existing tested IDs. Remove only unused venue-badge CSS and adjust layout rules if required.
- **Verification:** Run `tests/test_portfolio_card_basis.py` and the related headline/overview tests; inspect the Performance & Analytics Portfolio Overview at desktop and narrow viewport widths.

### Task 6 — Review regression coverage and documentation contracts [Code/Test]
- **Files:** `tests/test_portfolio_headline_basis.py`, `tests/test_portfolio_overview.py` only if assertions require a behavior-preserving update; no backend files unless a test reveals an existing contract mismatch.
- **Build:** Keep backend KPI behavior unchanged and ensure wallet divergence/show-hide behavior remains covered.
- **Verification:** Run focused tests for the changed dashboard behavior. Do not run the full repository suite locally during Station IV/V; GitHub CI runs `python -m pytest -q` on Ubuntu and Windows as the merge gate.

## Acceptance checklist
- [x] Header layout matches the issue and existing DOM IDs remain intact.
- [x] Production chart has no synthetic curve path.
- [x] Real close points, start anchor, and final current point render correctly.
- [x] Timestamp labels, Current label, dotted baseline, empty state, tooltip, and crosshair are covered.
- [x] Focused dashboard tests pass after each relevant task and after review fixes.
- [ ] Browser verification is recorded for the visual change in Station IV.
- [ ] Full regression is verified by GitHub CI before merge.

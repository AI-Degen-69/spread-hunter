# Noticed-but-not-touching — Issue #535 (Station IV)

Issue #535 consolidates the confidence-interval multiplier onto one table and threads it through
the payload, so the distribution chart and the robustness card cannot publish different bands over
the same closes. It changes no writer and no sampling logic, so the three candidates below were
recorded rather than fixed here. They are all in the same chart, in the arms #535 did not set out
to change: one draws from list indices, one confirms an edge it never tested, and one can never
draw at all.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|----|-----------|---------------------|----------|--------|------------|
| N1 | **The outcome-probability arm draws its histogram and scatter points from list indices, not from measurements.** The arm builds `rawValues` from the measured bell and then overwrites them from the loop index, so a run with no fills still draws a distribution and the labels render `undefined`. | IV (agent fallback review, round 1) | `dashboard/static/app.js:3216-3217` (`flatMap` over `pb.bins`) and the `delta = 45.0` that scales it at `:3219`, overwritten at `:3221` (`rawValues = positions.map((_, i) => ((i * 17 + 23) % 70 + 15))`) | published | [#538](https://github.com/AI-Degen-69/spread-hunter/issues/538) |
| N2 | **The same arm's badge reports `EDGE CONFIRMED` whenever its lower bound is above zero**, which on a 0–100% axis is nearly always true, so it confirms an edge off a one-sided test it never performed. | IV (agent fallback review, round 1) | `dashboard/static/app.js:3262` (`const isPos = ciLower > 0`) and `:3273` | published | [#538](https://github.com/AI-Degen-69/spread-hunter/issues/538) |
| N3 | **The Spread Cost arm can never draw.** The chart reads `positions[].spread_cost` and `position_returns.positions` never carries that field — the keys sent for a close are `condition_id`, `market_slug`, `method`, `pnl_pct`, `pnl_usd`, `ts`, `type` — so selecting it shows `metric not recorded` on every run while `pair_costs` in the same payload holds the prices the arm is meant to show. Its `delta = 0.035` axis half-width is therefore unreachable through the page, though it is the right value and the renderer test pins it. | IV (agent fallback review, round 1) | `dashboard/static/app.js:3206` vs `core_brain/statistical_analytics.py::position_returns` (no `spread_cost` key), confirmed against a demo store built by `scripts/build_demo_db.py` | published | [#538](https://github.com/AI-Degen-69/spread-hunter/issues/538) |

Nothing else the review touched needed a change on this branch: the chart holds no threshold of its
own now, `kpi.py` reads its multipliers from the same table, and the one regression this branch did
introduce — the pair-cost arm losing its `delta = 0.035` when its `z` line was replaced — was found
by the same review and fixed here, with all four arms' axis scales pinned in
`tests/test_ci_critical_source.py`.

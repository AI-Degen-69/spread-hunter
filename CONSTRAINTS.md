# CONSTRAINTS — #514 (locked by Station II, enforced through Station V)

Governs the `i514/add-rescue-friction-analytics-for-stop-loss-drag` branch only.

## Scope & File Boundaries

- Target files for modification:
  - `core_brain/statistical_analytics.py` (new `STOP_OFFSET_FALLBACK`, `FRICTION_BAND_METRICS`
    constants, new `rescue_drag()` / `taker_cost_drag_ratio()` / `overshoot_ratio()` /
    `friction_band()` / `rescue_friction_summary()` functions, module docstring paragraph, and
    the single new key inside `build()` — nothing else on that module).
  - `tests/test_rescue_friction_analytics.py` (NEW — the ticket's required focused file).
  - `tests/test_statistical_analytics.py` (extend the populated and empty `build()` tests only).
  - `dashboard/static/index.html` (one new `#rescue-friction-readout` card after the
    `#fill-path-edge-readout` card).
  - `dashboard/static/app.js` (`renderRescueFriction`, its call inside
    `renderAnalyticsSurface`, and its entry in the `module.exports` object — nothing else).
  - `tests/js/analytics_surface_harness.cjs` (stub id, direct render call, printed HTML).
  - `tests/test_analytics_surface_mount.py` (new cases for the readout).
  - `CONSTRAINTS.md`, `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - `core_brain/kpi.py`, `core_brain/order_registry.py` (the `closes` schema and `CloseRecord`
    already carry `trigger_bid` / `fresh_bid` / `fee` — nothing to add), `core_brain/config.py`,
    `core_brain/unhedged_stop_loss.py`, `core_brain/venue_fees.py`,
    `core_brain/pnl_crosscheck.py`, `core_brain/registry_state.py`,
    `statistical_validation_run/artifacts.py` (`report.json` picks the new key up for free),
    `scripts/rescue_exit_report.py`.
  - Live execution: `core_brain/order_manager.py`, `core_brain/trader_loop.py`,
    `core_brain/single_buy_saver.py`, `core_brain/single_leg_lifecycle.py`, `core_brain/ladder.py`,
    `core_brain/shadow_exec.py`, `core_brain/shadow_fills.py`, `core_brain/market_resolution.py`.
  - `dashboard/server.py`, `core_brain/statistics_report.py`, `dashboard/static/styles.css`
    (the traffic-light palette already exists — see below).
  - `data/orders.db` and every production store (read-only at all times).

## Zero regressions

- Focused suites that must pass:
  `tests/test_rescue_friction_analytics.py`
  `tests/test_statistical_analytics.py`
  `tests/test_fill_path_analytics.py`
  `tests/test_analytics_surface_mount.py`
  `tests/test_dashboard_server.py`
  `tests/test_hard_stop_fresh_exit.py`
  `tests/test_pnl_by_fill_path.py`
  `tests/test_stat_validation_artifacts.py`
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every new behavior needs a test that fails without the change (RED first, per
  `test-driven-development`).

## Metric contract freeze

- Population: closes whose `method` (lowercased, stripped) is in the existing
  `statistical_analytics.RESCUE_METHODS` — `single_buy_exit` / `naked_exit` / `ladder_exit`.
  Filter by `method` only, never by `reason` (settled operator decision).
- Five metrics, in cents and ratios only. Percent/bps forms are NOT built (the ticket shows
  them "where shown", and no readout shows them).
  - `sled_cents` = `trigger_bid - exit_price`, in cents. Unclipped sign.
  - `latency_drag_cents` = `trigger_bid - fresh_bid`, in cents. Unclipped sign. Measured only
    when BOTH bids are recorded.
  - `execution_drag_cents` = `fresh_bid - exit_price`, in cents. Unclipped (the ladder walk
    makes it non-negative in theory, but a clipped number would be invented data).
  - `tcdr` = `fee / |realized_pnl|`. Denominator is the recorded payoff, not a fee-adjusted net.
  - `overshoot_ratio` = `sled_cents / planned_risk_cents`, `planned_risk_cents =
    (entry_price - reference_bid) * 100`.
- Additivity: `sled_cents` is literally the sum of the two returned hops when both are
  measured, so the identity holds exactly on the returned values.
- Reference price: recorded `trigger_bid` (source `recorded`); when absent,
  `entry_price - STOP_OFFSET_FALLBACK` (0.04) with source `assumed_offset`. `fresh_bid` is
  NEVER used as the stop reference.
- Every unmeasurable value is `None`, with a reason string where one exists (`overshoot_ratio`
  returns a `status`; the summary returns `unmeasured` bands). Missing is never `0`, `NaN` or
  `Infinity`; output is `json.dumps(..., allow_nan=False)`-safe.
- No function mutates its input. `dict` closes and `CloseRecord` objects measure identically.
- No fee recovery from `venue_fees.recover_fee`: the `closes` table has no `usdcSize`, so a
  recovered fee would be fabricated. A `NULL` fee stays unmeasured.
- `build()` keeps every one of its existing keys and values; the new key is additive.
- No classification, quoting, sizing, stop-trigger or GO-gate change. Analytics only.

## Traffic-light freeze

- Band thresholds live in Python, in `friction_band()`, and reach the dashboard as data
  (`bands` map in the payload). The renderer holds no threshold numerals.
- Bands are exactly the ticket's table: SLED `<= 0` / `(0, 0.02]` / `> 0.02`; latency `>= 0` /
  `(-0.02, 0)` / `<= -0.02`; execution `<= 0.02` / `(0.02, 0.05]` / `> 0.05`; TCDR `= 0` /
  `(0, 0.10)` / `>= 0.10`; overshoot `= 0` / `(0, 1.0]` / `> 1.0`. `None` → `unmeasured`.
- Colors come from the theme's existing status palette only: `.analytics-chip.live` (green),
  `.analytics-chip.warn` (amber), `.analytics-chip.bad` (red), neutral chip for `unmeasured`.
  No new CSS hue, no new class family.

## Anti-cheat

- No test may be deleted, skipped, `xfail`ed or weakened to reach green.
- No assertion may be loosened to `is not None` where an exact value is known.
- No `# type: ignore`, `# noqa`, or lint suppression may be added.
- No `pytest.mark.skipif` beyond the repo's existing `requires_node` marker idiom.
- A guard branch (`planned_risk <= 0`, `|realized_pnl| <= 1e-9`) must be covered by a test that
  would fail if the guard were removed (i.e. the case must raise `ZeroDivisionError` without it).

## Dependencies

- No new external dependency. Standard library only (`math`, `statistics`, `typing` — all
  already imported in the target module).

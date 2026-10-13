# CONSTRAINTS — #515 (locked by Station II, enforced through Station V)

Governs the `i515/add-queue-realism-analytics` branch only. The #514 contract this file
replaced is branch-scoped by its own header line and stays readable in git history.

## Scope & File Boundaries

- Target files for modification:
  - `core_brain/statistical_analytics.py` — new `ClearedFill` dataclass, new
    `TRADE_THROUGH_GOOD_BELOW_CENTS` / `TRADE_THROUGH_AVERAGE_AT_OR_BELOW_CENTS` constants,
    new `_percentile` / `trade_through_depth_band` / `trade_through_depth` / `fill_latency`
    functions, one new module-docstring paragraph, and the `dataclasses` import — nothing else
    in that module. **`build()` keeps every key and value it has today; no key is added.**
  - `tests/test_queue_realism_analytics.py` (NEW — the ticket's required focused file).
  - `docs/issues/515-noticed-but-not-touching.md` (NEW — the recorded follow-up).
  - `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - Fill logic and the crediting path: `core_brain/shadow_fills.py`,
    `core_brain/shadow_exec.py`, `core_brain/live_fill_engine.py`.
  - Readers and payloads that must keep reading exactly as they do now:
    `core_brain/kpi.py`, `core_brain/order_registry.py`, `core_brain/statistics_report.py`,
    `core_brain/markout.py`, `core_brain/cancel_report.py`, `scripts/queue_report.py`,
    `scripts/booktape_report.py`, `scripts/family_probe_report.py`,
    `statistical_validation_run/artifacts.py`.
  - The dashboard and the API in front of it: `dashboard/` (including `static/app.js`,
    `static/index.html`, `static/styles.css`), `dashboard/server.py`, `server/`, `server.ts`.
    This ticket has no readout and no card.
  - LIVE execution: `core_brain/order_manager.py`, `core_brain/trader_loop.py`,
    `core_brain/single_buy_saver.py`, `core_brain/ladder.py`, `core_brain/risk.py`,
    `core_brain/single_leg_lifecycle.py`, `core_brain/market_resolution.py`,
    `core_brain/config.py`.
  - Sibling telemetry sections from #513 / #514 and their tests — extended by nobody here.
  - `SPEC.md` — it is #514's merged artifact; this Small-tier issue embeds its spec in
    `tasks/plan.md` instead.
  - `data/orders.db` and every production store (read-only at all times).

## Zero regressions

- Focused suites that must pass on this branch:
  `tests/test_queue_realism_analytics.py`
  `tests/test_statistical_analytics.py`
  `tests/test_fill_path_analytics.py`
  `tests/test_rescue_friction_analytics.py`
  `tests/test_shadow_fills.py`
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every new behavior needs a test that fails without the change (RED first, per
  `test-driven-development`). Each task's tests are written before its implementation and the
  RED output is reported, not assumed.

## Metric contract freeze

- Population: caller-supplied `ClearedFill` records — one per credited shadow fill and the
  print that cleared its level. No database, tape, network or signer access anywhere in this
  change; the module imports only the standard library plus the existing `_finite`.
- `trade_through_depth_cents` = `(order_price - clearing_price) * 100`. A row is measured only
  when both prices are finite and `clearing_price < order_price`.
  Reported: `count`, `missing` (either price absent/non-finite), `not_trade_through`
  (`clearing_price >= order_price` — a row the fill model cannot produce), `median_cents`,
  `p75_cents`, `band`. Invariant: `count + missing + not_trade_through == len(fills)`.
- `fill_latency_sec` = `filled_ts - placed_ts`, in seconds. A row is measured only when both
  timestamps are finite and `filled_ts >= placed_ts`. Reported: `count`, `excluded`,
  `median_sec`, `p90_sec`. Invariant: `count + excluded == len(fills)`.
  - `placed_ts` maps to `quotes.ts`; `filled_ts` maps to the credited fill's time in SECONDS
    (`quotes.fill_ts` / `fills.venue_ts` are milliseconds — the caller divides by 1000, exactly
    as `kpi.py:1447-1449` does). This module receives seconds and never converts.
- Percentiles: linear interpolation at index `fraction * (n - 1)`, so `p50` equals
  `statistics.median`. One value returns itself. Zero values returns `None`.
- Missing is never `0`, `NaN` or `Infinity`, and nothing is clamped: a negative wait is
  excluded and counted, NOT clamped to `0.0` the way `kpi.median_seconds_to_fill` clamps.
  Output is `json.dumps(..., allow_nan=False)`-safe and rounded to 6 decimals.
- No function mutates its input.
- The two metrics are independent: prices present without timestamps still measure for depth
  and are `excluded` from latency, and the reverse.

## Traffic-light freeze

| metric | band | thresholds |
| --- | --- | --- |
| trade-through depth (median, cents) | `good` | `[0, TRADE_THROUGH_GOOD_BELOW_CENTS)` = `[0, 1.0)` |
| | `average` | `[1.0, TRADE_THROUGH_AVERAGE_AT_OR_BELOW_CENTS]` = `[1.0, 3.0]` |
| | `bad` | `(3.0, ∞)` |
| | `unmeasured` | the median is `None` or non-finite |
| fill latency | — | **no band exists.** A wait has no market reference to be judged against. |
| `queue_ahead` / queue depth | — | **no band exists.** It stays descriptive telemetry; this ticket adds no queue metric at all. |

The thresholds live in `core_brain/statistical_analytics.py` and reach any reader as data
(`trade_through_depth_band()`, and `band` inside the depth dict). No numeral from this table
may be hardcoded in a renderer, a script, a test fixture's expectation copy, or the docs.

## Anti-cheat

- No test is skipped, deleted, or weakened to reach green; no new `pytest.mark.skip` /
  `xfail`, no swallowed exceptions, no `# type: ignore`, no narrowed assertion to fit output.
- No new external dependency: standard library only, plus the module's existing `_finite`.
- No change to any existing function's return shape, and no change to `build()`'s keys.
- No new config, flag, CLI argument, dashboard element, or report script.
- A test that passes both with and without the change is not a test for this ticket.

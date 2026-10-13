# CONSTRAINTS — #515 (locked by Station II, enforced through Station V)

Governs the `i515/add-queue-realism-analytics` branch only. The #514 contract this file
replaced is branch-scoped by its own header line and stays readable in git history.

## Scope change — operator-approved 2026-10-14, after the three tasks landed

The Owner asked why #515 had no readout when #513 and #514 both shipped a card on the
Performance & Analytics tab, and chose: **card now, latency live and depth pending.** The
issue's "no DB or tape ingestion" decision is therefore lifted for exactly one thing —
building `ClearedFill` rows from `quotes` + `fills` — and nothing else. Two guardrails below
are amended by that decision and are marked so; every other line still stands.

## Scope & File Boundaries

- Target files for modification:
  - `core_brain/statistical_analytics.py` — new `ClearedFill` dataclass, new
    `TRADE_THROUGH_GOOD_BELOW_CENTS` / `TRADE_THROUGH_AVERAGE_AT_OR_BELOW_CENTS` constants,
    new `_percentile` / `trade_through_depth_band` / `trade_through_depth` / `fill_latency` /
    `cleared_fills` functions, one new module-docstring paragraph, and the `dataclasses`
    import. **AMENDED:** `build()` keeps every key and value it has and gains exactly one
    additive key (`queue_realism`) plus one optional trailing parameter (`quotes`).
  - `core_brain/kpi.py` — **AMENDED:** one call site, `build_statistical_analytics(...)`
    gains `quotes`. No other line of that module changes, and no payload key other than
    `statistical_analytics.queue_realism` is added.
  - `dashboard/static/index.html` — one new `#queue-realism-readout` card after the
    `#rescue-friction-readout` card.
  - `dashboard/static/app.js` — `renderQueueRealism`, its call inside
    `renderAnalyticsSurface`, and its entry in the `module.exports` object. Nothing else.
  - `tests/js/analytics_surface_harness.cjs` — stub id, direct render call, printed HTML.
  - `tests/test_analytics_surface_mount.py` — new cases for the readout.
  - `tests/test_queue_realism_analytics.py` (NEW — the ticket's required focused file).
  - `tests/test_statistical_analytics.py` — the populated and empty `build()` tests only.
  - `docs/issues/515-noticed-but-not-touching.md` (NEW — the recorded follow-up).
  - `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - Fill logic and the crediting path: `core_brain/shadow_fills.py`,
    `core_brain/shadow_exec.py`, `core_brain/live_fill_engine.py`. Nothing records the
    clearing print in this branch — that stays the recorded follow-up, not a smuggled change.
  - Readers and payloads that must keep reading exactly as they do now:
    `core_brain/order_registry.py`, `core_brain/statistics_report.py`,
    `core_brain/markout.py`, `core_brain/cancel_report.py`, `scripts/queue_report.py`,
    `scripts/booktape_report.py`, `scripts/family_probe_report.py`,
    `statistical_validation_run/artifacts.py`.
  - The dashboard's other surfaces: `dashboard/server.py`, `dashboard/static/styles.css`,
    `server/`, `server.ts`. The traffic-light palette already exists in the theme; no CSS is
    added, and no server route changes.
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
  `tests/test_analytics_surface_mount.py`
  `tests/test_kpi.py`
  `tests/test_dashboard_server.py`
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
- `cleared_fills(quotes, fills)` is pure over supplied rows and joins on
  `fills.order_uuid == quotes.local_id`. One record per quote a fill credits: a quote that
  never filled is left out (there is nothing to measure), while a fill with no recorded
  timestamp is KEPT with `filled_ts = None` (a fill that happened must not vanish).
  `filled_ts` converts milliseconds to seconds here, once. `clearing_price` is `None` on
  every record this branch can build, because no writer stores it yet.
- `build()` gains exactly one additive key, `queue_realism`, always present:
  `{cleared_fill_count, trade_through_depth{...}, fill_latency{...}}`.

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

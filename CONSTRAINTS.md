# CONSTRAINTS — #453 (locked by Station II, enforced through Station V)

Governs the `i453` branch only; #448 constraints below stay as history.

## Zero regressions

- Focused suites that must pass:
  `tests/test_single_leg_lifecycle.py`
  `tests/test_single_buy_saver.py`
  `tests/test_stray_guard.py`
  `tests/test_trader_loop.py`
  `tests/test_plan_orders_asymmetric_hold.py`
  `tests/test_plan_orders_mid_hold.py`
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every new behavior needs a test that fails without the change (RED first, per `test-driven-development`).

## Scope & File Boundaries

- Target files for modification:
  `core_brain/single_leg_lifecycle.py`
  `core_brain/single_buy_saver.py`
  `core_brain/stray_guard.py`
  `core_brain/trader_loop.py`
  `core_brain/config.py`
  `SPEC.md`
  `CONSTRAINTS.md`
  `tasks/plan.md`
  `tasks/todo.md`
  And test files under `tests/`.
- Files that must NOT be modified:
  `core_brain/risk.py`
  `core_brain/shadow_exec.py` (and shadow client files)
  `core_brain/order_registry.py`
  `data/orders.db`

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies without explicit approval.
- Pure exposure logic uses `SIZE_EPS` for all float comparisons.
- No live orders; no `quote`, `complete`, Trader loop, or dashboard START.

---

# CONSTRAINTS — #448 (history)

Governs the `i448` branch only; #443 constraints below stay as history.

## Zero regressions

- Focused suites that must pass:
  `tests/test_kpi.py`
  `tests/test_statistical_analytics.py`
  `tests/test_mean_pnl_ci.py`
  `tests/test_trade_analytics.py`
  `tests/test_analytics_api.py`
  `tests/test_analytics_surface_mount.py`
  `tests/test_analytics_impact_tiers.py`
  `tests/test_negative_values_read_as_losses.py`
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every new behavior needs a test that fails without the change (RED first, per `test-driven-development`).

## Scope & File Boundaries

- Target files for modification:
  `core_brain/kpi.py`
  `dashboard/static/app.js`
  `dashboard/static/styles.css`
  `tests/js/analytics_surface_harness.cjs`
  `tests/test_kpi.py`
  `tests/test_analytics_api.py`
  `tests/test_analytics_surface_mount.py`
  `tests/test_negative_values_read_as_losses.py`
  `SPEC.md`
  `CONSTRAINTS.md`
  `tasks/plan.md`
  `tasks/todo.md`
- No modification permitted to live execution or live order pricing logic:
  `core_brain/config.py`
  `core_brain/order_manager.py`
  `core_brain/order_registry.py`
  `data/orders.db`

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies.

---

# CONSTRAINTS — #443 (history)

## Zero regressions

- Focused suites that must pass:
  `tests/test_kpi.py`
  `tests/test_trade_analytics.py`
  `tests/test_mean_pnl_ci.py`
  `tests/test_stat_gate.py`
  `tests/test_analytics_api.py`
  `tests/test_analytics_surface_mount.py`
  `tests/test_analytics_impact_tiers.py`
  `tests/test_negative_values_read_as_losses.py`
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every new behavior needs a test that fails without the change (RED first, per `test-driven-development`).

## Scope & File Boundaries

- Target files for modification:
  `core_brain/kpi.py`
  `dashboard/static/index.html`
  `dashboard/static/app.js`
  `dashboard/static/styles.css`
  `tests/js/analytics_surface_harness.cjs`
  `tests/test_kpi.py` (new)
  `tests/test_analytics_api.py`
  `tests/test_analytics_surface_mount.py`
  `tests/test_analytics_impact_tiers.py`
  `tests/test_negative_values_read_as_losses.py`
  `SPEC.md`
  `CONSTRAINTS.md`
  `tasks/plan.md`
  `tasks/todo.md`
- No modification permitted to core trading/orders or other modules:
  `core_brain/config.py`
  `core_brain/order_manager.py`
  `core_brain/order_registry.py`
  `data/orders.db`
  `dashboard/server.py`

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies.

---

# CONSTRAINTS — #441 (history)

---

# CONSTRAINTS — #435 (history)

## Zero regressions

- Focused suites that must pass:
  `tests/test_redesigned_filter_pipeline.py`
  `tests/test_pipeline_snapshot_gates.py`
  `tests/test_unified_universe.py`
  `tests/test_velocity_gate.py`
  `tests/test_in_play_gate.py`
  `tests/test_rank_score.py`
  `tests/test_order_manager_decide.py`
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every new behavior needs a test that fails without the change (RED first, per `test-driven-development`).

## Scope & File Boundaries

- New test file: `tests/test_redesigned_filter_pipeline.py`.
- No modification permitted to production logic or schemas:
  `scripts/filter_markets.py`
  `scoring/selector.py`
  `scoring/config.py`
  `core_brain/order_manager.py`
  `core_brain/order_registry.py`
  `core_brain/markets.py`
  `core_brain/market_feed.py`
  `core_brain/quotes.py`
  `data/orders.db`
  `tests/conftest.py`
- Tests must be strictly offline:
  - Stub HTTP calls via fake sessions/responses (`_FakeSession`, `_Resp`).
  - Capture request order in a log to prove fail-fast behavior.
  - Temporary files (`tmp_path`) for SQLite databases and feed files.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies.
- No live orders; no `quote`, `complete`, Trader loop, or dashboard START.

---

# CONSTRAINTS — #434 (locked by Station II, enforced through Station V)

Governs the `i434` branch only; #433 constraints below stay as history.

## Zero regressions

- Focused suites that must pass:
  `tests/test_velocity_gate.py`
  `tests/scoring/test_markets.py`
  `tests/test_unified_universe.py`
  `tests/test_live_quotes.py`
  `tests/test_trader_loop.py`
  `tests/test_market_selection_bars.py`
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every new behavior needs a test that fails without the change (RED first, per `test-driven-development`).

## Volatility Gate Parameters & Exemptions

- Lookback window: 2 hours (`7200.0` seconds), configurable via `select_volatility_window_sec` in `scoring/config.py` (env `HUNTER_VOLATILITY_WINDOW_SEC`, default `7200.0`). Traded volume movement window remains strictly 30m (`1800.0` seconds).
- Minimum range/swing bar: strictly `2.0c` (`$0.02`), configured via `select_min_range_cents = 2.0` (env `HUNTER_MIN_RANGE_CENTS`, default `2.0`).
- Rejection reason: exact format `flat market: price swing {r_str} in last {minutes_or_hours} < {min_range_cents:.2f}c` (e.g. `flat market: price swing 0.50c in last 2h < 2.00c`).
- Sports & eSports live exemption:
  - Reusable helper `is_sports_or_esports` in `scoring/selector.py` matching sports/esports keywords (`sports_market_type` or `_SPORTS_SERIES_RE` across title, slug, series, category).
  - When market has active live event signal (`_live_event` or `live_event: true`) AND `is_sports_or_esports(...)`:
    - Range/swing gate is skipped (`range_exempt=True` in `velocity_gate_reject`).
    - Market row receives `volatility_exempt = True`.
    - Note: trade count and last trade latency checks in `velocity_gate_reject` are NOT skipped.
- Sorting priority:
  - In `sort_eligible` (`scripts/filter_markets.py`), eligible markets sort primarily by `volatility_exempt` (exempt/live sports first: `not r.get("volatility_exempt")`), and secondarily by `-rank_score(r)`.
- Boundary freezes:
  - Book depth gate ($500), 24h volume gate ($125k / $10k live), max spread gate (0.0205), and decided mid band [0.15, 0.85] remain strictly untouched.
  - Zero live venue orders, zero edits to `data/orders.db`.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration; tests use temporary state or fakes.
- No live orders; no `quote`, `complete`, Trader loop, or dashboard START.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies without explicit operator approval.
- Tests stay isolated from the real network (stub HTTP with `_TapeSession` / `_FakeResponse`).

---

# CONSTRAINTS — #433 (locked by Station II, enforced through Station V)

Governs the `i433` branch only; #432 constraints below stay as history.

## Zero regressions

- Focused suites that must pass:
  `tests/test_research_market_metrics.py`
  `tests/test_live_events_probe.py`
  `tests/scoring/test_markets.py`
  `tests/test_market_selection_bars.py`
  `tests/test_data_retention.py`
  `tests/test_report_destination_is_root_anchored.py`
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every new behavior needs a test that fails without the change (RED first, per `test-driven-development`).

## Read-only Research Scope & Isolation

- Standalone telemetry script: `scripts/research_market_metrics.py` only collects empirical distributions.
- Zero venue state alteration: no orders, no cancellations, no writes to `data/orders.db` or `runtime/`.
- Zero changes to production screening thresholds, gates, or order managers (`core_brain/config.py`, `scoring/config.py`, `core_brain/quotes.py`, `scripts/filter_markets.py` remain untouched).
- Reuses existing domain helpers without side effects: `scoring.selector.top_depth_usd`, `scoring.markets.full_book`, `scripts.live_events_probe._event_list`.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration; tests use temporary state or fakes.
- No live orders; no `quote`, `complete`, Trader loop, or dashboard START.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies without explicit operator approval; `requests` is already declared in `requirements.txt`.
- Tests stay isolated from the real network (stub HTTP with `_FakeResponse` / `_FakeSession`).

---

# CONSTRAINTS — #432 (locked by Station II, enforced through Station V)

Governs the `i432` branch only; #431 constraints below stay as history.

## Zero regressions

- Focused suites that must pass:
  `tests/test_live_quotes.py`
  `tests/test_trader_loop.py`
  `tests/test_unified_universe.py`
  `tests/test_wide_book_trial.py`
  `tests/test_live_event_discovery.py`
  `tests/test_market_selection_bars.py`
  `tests/test_pipeline_snapshot_gates.py`
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every new behavior needs a test that fails without the change (RED first, per `test-driven-development`).

## Gate Parameter Freezes & Boundaries

- Price band gate: strictly set to `[0.15, 0.85]` across quoting (`core_brain/quotes.py`) and screening (`scripts/filter_markets.py`). Refusal messages explicitly specify `outside [0.15, 0.85]`.
- Max book spread gate: strictly set to `0.0205` in `core_brain/config.py`, `scoring/config.py`, `scoring/selector.py`, and `scripts/filter_markets.py`.
- Float precision guard: in `scoring/selector.py:book_allowed`, spread must be rounded to 6 decimal places before checking `spread > max_spread` so `0.5105 - 0.49` is not rejected.
- Out of scope & frozen: volume gates (`MIN_VOLUME_24H`), depth gates (`MIN_TOP3_DEPTH_USD`), velocity gates (`select_min_range_cents`, `select_min_movement_usd`), horizon gate (30 days), and runtime book health spread (`max_book_spread = 0.06`).
- `HUNTER_WIDE_BOOK_TRIAL` parser and trial mechanisms remain untouched.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration; tests use temporary DBs or isolated state.
- No live orders; no `quote`, `complete`, Trader loop, or dashboard START.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies without explicit operator approval.
- Tests stay isolated from the real network (stub HTTP).

---

# CONSTRAINTS — #431 (locked by Station II, enforced through Station V)

Governs the `i431` branch only; #427 constraints below stay as history.

## Zero regressions

- Focused suites that must pass:
  `tests/test_pipeline_snapshot_gates.py`
  `tests/test_order_manager.py`
  `tests/scoring/test_markets.py`
  `tests/test_unified_universe.py`
  `tests/test_single_buy_saver.py`
  `tests/test_pre_start_gate.py`
  `tests/test_dashboard_server.py`
  `tests/test_live_funnel.py`
  `tests/test_market_quote.py`
  `tests/test_markout_maturity.py`
  `tests/test_milestone7_telemetry.py`
  `tests/test_shadow_markouts.py`
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every new behavior needs a test that fails without the change (RED first, per `test-driven-development`).

## Horizon and Trading Gate Safety Freeze

- The resolution horizon gate (events closing within 30 days / `MAX_DAYS_TO_RESOLVE`) remains active, unchanged, and strictly enforced.
- Spread calculation, depth gates, velocity gates, and dynamic risk caps remain untouched.
- Quote pricing (`core_brain/quotes.py`) and order execution remain untouched.
- `tradable()` and scoring geometry remain untouched.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration; tests use temporary DBs or isolated state.
- No live orders; no `quote`, `complete`, Trader loop, or dashboard START.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies without explicit operator approval.
- Tests stay isolated from the real network (stub HTTP).

---

# CONSTRAINTS — #427 (locked by Station II, enforced through Station V)

Governs the `i427` branch only; #398 constraints below stay as history.

## Zero regressions

- Focused suites that must pass: `tests/test_live_marks.py` (new),
  `tests/test_dashboard_server.py`, `tests/test_positions_live_marks.py` (new),
  `tests/test_dashboard_poll_budgets.py`.
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only
  the focused suites.
- Every new test must fail without the change (RED first). Browser harness
  tests run via the repo's existing node harness pattern.

## Feed-budget freeze

- Wanted-set path adds no `full_book()` calls, no KPI rebuilds, no feed reads
  — `tests/test_dashboard_poll_budgets.py` is the contract.
- No venue work at import time or in stream generators; worker starts only in
  the CLI startup path. Tests and `TestClient` sessions stay offline.
- No `decide_quotes`, pricing, routing, registry, or pair-merging changes.
- `core_brain/kpi.py`, `core_brain/registry_state.py`: untouched (cost basis
  already flows through `by_market`).

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration; tests use temporary DBs.
- No live orders; no `quote`, `complete`, Trader loop, dashboard START, merge,
  or other trading action — including during hands-on `sim` verification.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies without explicit operator approval
  (`websocket-client` is already required).

---

# CONSTRAINTS — #398 (locked by Station II, enforced through Station V)

Governs the `i398` branch only; #422 constraints below stay as history.

## Zero regressions

- Focused suites that must pass: `tests/test_maker_queue_bar.py`,
  `tests/test_queue_clear_gate.py`.
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only
  the focused suites.
- Every new assertion needs a reason to fail (pins the documented contract).

## Bar-value freeze

- `select_max_queue_minutes`, `max_queue_clear_minutes`,
  `queue_flow_window_sec`: values untouched. No retuning.
- `enforce_max_queue_minutes`, `enforce_queue_clear_gate`: flags untouched.
  Both ship record-only; flipping either is its own operator decision.
- No pricing, routing, registry, or shadow-seam changes — comments, record,
  and test pins only.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration; tests use temporary DBs.
- No live orders; no `quote`, `complete`, Trader loop, or dashboard START.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies without explicit operator approval.

---

# CONSTRAINTS — #422 (locked by Station II, enforced through Station V)

Supersedes the previous issue plan constraints for this branch only.

## Zero regressions

- Focused suites that must pass for the touched behavior:
  `tests/test_shadow_run.py` (plus `tests/test_shadow_run_run_id.py` style
  coverage if the CLI test lands there).
- Existing paired tests stay unchanged in meaning, including
  `test_admission_arm_requires_markets_path_and_bankroll`.
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only
  the focused suites.
- Every behavior change needs a test that fails without the change
  (RED first, per `test-driven-development`) — notably a test that fails
  if the live balance read returns.

## Bankroll-source freeze

- `core_brain/config.py`: bankroll default (line 41), env overrides
  (1521-1528), and bounds: untouched.
- `derive_dynamic_caps` and `trader_loop._fleet_state`: untouched —
  no live trading path changes.
- Early paired checks (`shadow_run.py:902-911`, mutual exclusion +
  markets-path + explicit-bankroll requirements): unchanged.
- Paired consumption (`shadow_run.py:1013-1034`, `record_paired_run_start` /
  `record_paired_equity_mark`): unchanged.
- Validation error text must still contain "bankroll" so existing
  test matches keep passing.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration, no database rewriting; tests use temporary DBs.
- Shadow rehearsals use a fresh unique store per run
  (`data/NN_shadow_<stamp>.db`); `data/orders.db` is refused as a store.
- No live orders are opened; no `quote`, `complete`, Trader loop,
  or dashboard START.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies without explicit operator approval.
- `statistical_validation_run/run.py` and `scripts/shadow_tournament.py`:
  untouched (verified out of scope, see SPEC.md).

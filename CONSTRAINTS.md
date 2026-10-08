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

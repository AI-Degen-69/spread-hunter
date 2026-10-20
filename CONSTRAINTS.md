# Quality Guardrails & Constraints - Issue #705

Branch: `i705/dashboard-parameter-card-suffix` | Issue: `#705`

- **Zero regressions:** the focused suite `tests/test_dashboard_server.py` must stay green
  (this is the targeted suite for the touched module). No test skipped, deleted, or weakened.
  The `test_parameters_endpoint_emits_no_deprecation_warnings` and
  `test_parameters_cards_canonical_env_var_badges` (59-card count) guards must keep passing.
- **Scope fence:** changes confined to `dashboard/server.py` value-format strings (15
  suffix removals) and `tests/test_dashboard_server.py` (one repin with comment, one new
  test). No changes to `core_brain/config.py`, `dashboard/static/app.js`,
  `strategy_explainer.html`, quoting logic, or money-path configuration values
  (`max_book_spread`, `max_pair_cost`, `select_spread_pct`, trial bound, `MAX_BOOK_SPREAD`).
- **Classification rule (locked):** remove a value-string literal only when it restates the
  default in another unit. Literals carrying information the bare number does not
  (`(50% of spread)`, `(Telemetry Only)`) stay — recorded deliberately, not regressions.
- **No cheating:** no skipped tests, no deleted assertions, no linter suppressions.
- **Dependencies:** none added.
- **Safety:** no live/venue commands, no writes to `data/orders.db`, no direct push to `main`.

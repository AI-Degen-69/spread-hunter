# Constraints: Issue #337 — ENGINE pill provenance, stall reason, and other-store live run notice

## Quality & Tests
- Zero regressions: `tests/test_per_run_shadow_heartbeat.py`, `tests/test_scan_state_shadow.py`, `tests/test_live_state_language.py`, `tests/test_dashboard_server.py`, `tests/test_trader_loop.py`, `tests/test_shadow_run.py` stay 100% green. Full-repo sweep stays with GitHub CI on push.
- New behaviour requires tests RED against untouched code and GREEN after; each added assertion must fail without its change.
- Anti-cheat: strictly forbid skipping tests, deleting or weakening assertions, or bypassing linters. Tests use `tmp_path` fixtures; no test writes into live `data/` or `run/`. `data/orders.db` is never touched.

## Behaviour Boundaries
- **Db-scoped rule preserved**: A heartbeat for another store is never surfaced as the primary active run or stopwatch on the current page (`read_shadow_run` maintains active store matching).
- **Other-store reader isolation**: `read_other_live_shadow_runs` returns only identity (`run_id`, `db_path`, `heartbeat_file`) and zero performance/age/cycle metrics from other stores.
- **Fail-closed stall reasons**: `stall_reason` cleanly differentiates `finished`, `process_gone`, `heartbeat_stale`, `no_heartbeat`, and `heartbeat_unreadable`.
- **UI Neutrality**: The `#scan-engine-elsewhere` notice uses neutral dashed styling matching design tokens. No new color tokens.
- **Routine empty refresh log level**: When `markets_fn_empty_is_routine=True`, empty refresh logs as INFO; otherwise keeps WARNING.
- **No new external dependencies.**

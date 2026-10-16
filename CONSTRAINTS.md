# Constraints — #559 [T2] (sub-issue of #520, blocked by #558 CLOSED)

Branch: i559/consumers-import-taxonomy | Issue: #559

- **Frozen populations (bit-identical, read-only):** `market_resolution.py:654,685`, `paired_shadow.py:742`, `order_registry.py:2427`, `shadow_exec.py:1652`, `single_buy_saver.py:550`, `statistical_analytics.py:304,732`, `kpi.py:1143,1504`, execution funnel `kpi.py:980-981`. Any diff touching these lines rejects the change.
- **Only behavior deltas:** the three T1–T3 defect fixes plus new guard assertions (T4). Exactly one existing assertion may change: `tests/test_pnl_by_fill_path.py:61-73`.
- **Read-only:** `core_brain/close_methods.py`, `build_gate_rows` / `resolve_verdict`, `dashboard/static/app.js` (#560 owns it), everything in `data/` (temp stores only).
- **Zero regressions:** the five acceptance suites pass; all other accounting suites pass with no changed assertions. Full-repo sweep stays with GitHub CI.
- **Anti-cheat:** no skipped/disabled tests, no deleted assertions, no linter suppressions. The "1 against 65" gap is recorded as a follow-up note, never silenced.
- **Dependencies:** no new external dependencies.

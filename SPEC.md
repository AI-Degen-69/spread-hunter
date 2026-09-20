# SPEC: Issue #252 — Portfolio equity chart anchored to the run's DB start

## Goal
The Portfolio Overview card must describe the run, not the dashboard session. Its first point, its START baseline, its hero percentage and its Starting Bankroll tile all come from the same registry value: the newest `account_marks` row at or before the active run's first activity.

## Acceptance criteria (from issue)
- [ ] `report(... )["portfolio"]["starting_capital"]` is the newest `account_marks` row at or before the active run's first recorded activity (`list_runs()` `first_ts`), falling back in order: run's earliest mark → store's earliest mark → `_CFG.bankroll_usd`. On `data/01_shadow_12-09_00-58.db` that is `53.631665`.
- [ ] Same payload carries `portfolio.starting_capital_ts` = that mark's `ts` (`2026-09-11T21:58:29.910224Z` here), `null` when anchor is the config-bankroll fallback.
- [ ] `KPI_PAYLOAD_VERSION` and `EXPECTED_PAYLOAD_VERSION` bumped together and `tests/test_analytics_api.py:165` still passes.
- [ ] Chart's first point equals the anchor and its x-axis label is that anchor's real ISO timestamp (fallback `Start` only when no timestamp).
- [ ] Dashed baseline and `START: $…` sit at the anchor; not derived from `/api/system/status` `starting_capital`.
- [ ] `#broker-starting-cap`, hero pill % and KPI-grid Starting Capital tile read the same anchor — on the store above: equity `$62.04`, `+$8.41`, `+15.68%`, `Starting Bankroll: $53.63`, curve rises from `$53.63` to `$62.04`.
- [ ] Dashboard restart changes none of those four numbers.
- [ ] Degenerate store (no marks): anchor is config bankroll, label `Start`, SVG contains no `NaN`/`undefined`.
- [ ] `python -m pytest -q tests/test_portfolio_card_basis.py tests/test_portfolio_overview.py tests/test_account_kpi.py tests/test_analytics_api.py` green; full suite green in CI.

## Scope
### In scope
- `core_brain/kpi.py` anchor selection + `starting_capital_ts`, `equity_series` start point, payload version bump (`kpi.py:45`, 251→252).
- `dashboard/static/app.js` `portfolioEquity()` precedence fix, `buildBrokerEquitySeries()` Start point + label, chart baseline geometry, hero pill denominator, KPI-grid tile, `EXPECTED_PAYLOAD_VERSION` (`app.js:71`).
- `dashboard/static/index.html` `#broker-starting-cap` (id/label preserved, value changes).
- Tests: `tests/test_portfolio_card_basis.py` (+ harness `tests/js/portfolio_card_harness.cjs`), `tests/test_portfolio_overview.py`, `tests/test_account_kpi.py`, `tests/test_analytics_api.py`.

### Out of scope (per issue)
- Trading, quoting, sizing, order-registry schema, `data/orders.db`.
- `Venue wallet` row meaning/copy and the ~$11 top-up gap (stays as-is).
- Timeframe-control semantics (windowed frames keep pinning run-start at left edge; only value/date change).
- Deleting `runtime/processes.json` `starting_account_value` writer (stays, just not the card's baseline).
- New frontend dependencies.

## Edge cases
- No `account_marks` rows → fallback to `_CFG.bankroll_usd`, timestamp `null`, label `Start`, no NaN in SVG.
- Live registry (no active run) → same fallback chain; still one basis for whole card.
- Shadow store with resumed session (`runtime/shadow-session.json` `resumed:true`) → DB anchor still wins over session snapshot.

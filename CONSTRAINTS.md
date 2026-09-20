# Constraints: Issue #240 — End-of-window proximity gate

## Quality & Tests
- Zero regressions: full `python -m pytest -q` must remain green.
- Targeted suites must pass 100%: quote/decision tests (`tests/test_price_risk_widen_override.py` pattern), new endgame regression + config-validation + cadence tests.
- Anti-Cheat: strictly forbid skipping tests (`@pytest.mark.skip`), deleting assertions, or bypassing linters.
- New/changed behavior needs a test that fails without the change (gate-off posts the S&P pair; gate-on refuses/deepens it).

## Risk & Behavior Boundaries
- Gate defaults OFF (`enforce_endgame_gate=False`); no live behavior change until explicitly enabled.
- Unknown cadence (`None`) fails open — gate must not fire.
- Light-side balancing quotes are never blocked (`risk.naked_side` check).
- No signature changes to `decide_quotes` / `evaluate_market_quote` callers; no new timestamp source besides `LiveMarket.t_remaining()`; no new external dependencies.
- `window_frac=None` for graduated markets — never invent a window origin.
- `deepen` path reuses the existing offset pipeline + `max_spread_from_mid` clamp; no parallel pricing path.
- No `rehearsal.is_rehearsal()` gating on the new knobs (risk-tightening applies everywhere).

## Constraints: Issue #242 — Portfolio equity chart and header
- Zero regressions: focused dashboard tests covering modified files must pass; GitHub CI runs the full regression suite on push.
- New chart behavior must be covered by a test that fails when synthetic curve data is restored.
- Preserve the existing `broker-starting-cap`, `broker-venue-wallet-row`, `broker-venue-wallet`, and `broker-venue-wallet-note` ids and wallet rendering behavior.
- Do not change backend KPI calculations or add an endpoint; consume the existing top-level `kpi.equity_series` payload.
- Do not add external dependencies or change the chart timeframe/control contract.
- Do not skip or weaken existing assertions, and do not use fake/synthetic equity points in the production chart path.

## Constraints: Issue #248 — Expectancy vs mean-return audit
- Zero regressions: focused four-file selection (`tests/test_trade_analytics.py`, `tests/test_mean_pnl_ci.py`, `tests/test_seed_preview_fixture.py`, `tests/test_analytics_api.py`) must pass; full `python -m pytest -q` stays with GitHub CI.
- New/changed behavior needs a test that fails without the change (RED before GREEN): companion fields, regression fixture, explainer copy.
- Anti-Cheat: strictly forbid skipping tests, deleting assertions, or bypassing linters.
- Formulas `expectancy_usd`, `mean_return_pct`, `ci90_lower_pct` MUST NOT change; gate verdict (`passed`) provably identical. New fields are display-only companions.
- Missing or non-positive `cost_basis` stays excluded from percent calculations and renders as unmeasured — never a fabricated zero.
- Pinned dashboard tile labels ("Average Profit Per Close", "Mean Return Per Trade") stay unchanged; only explainer copy and sublabels added.
- No new external dependencies; no live-trading, execution, risk-cap, or threshold changes.


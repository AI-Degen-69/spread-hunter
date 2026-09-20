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

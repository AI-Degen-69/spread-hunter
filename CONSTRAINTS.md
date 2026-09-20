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

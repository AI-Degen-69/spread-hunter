# Constraints - #632 (parameter & env naming standardization)

Branch: main (planning) | Issue: #632

Replaces #628-scoped file wholesale (Step 0A.0: stale issue scope).

## Zero regressions
- Focused suites: `tests/test_quotes.py`,
  `tests/test_config_for_display.py`, `tests/test_trader_loop.py`,
  `tests/test_stat_gate.py` + each edited file.
- `tests/test_risk.py` (issue list) DOES NOT EXIST — build creates it
  or corrects the command and records which. Full suite = CI gate.
- Every rename needs a RED-first test.

## Deliberate contracts (not regressions)
- Legacy ctor keywords rejected with TypeError (fail-loud).
- KPI keys `"bankroll_fraction"`/`"kelly_fraction"` kept (shared format).
- `MAX_ORDER_USD`/`MAX_TOTAL_USD` kept (import contract); comment only.
- `threshold_pct`, `stat_gate_threshold_pct`, `*_return_pct` are
  percentage-points exceptions (1.0 = 1%), never rescaled.

## Anti-cheat
- No skipped/disabled tests, no weakened assertions, no silenced linters.
- No rescaling disguised as rename; defaults asserted verbatim.

## Production safety (LIVE default)
- `data/orders.db` read-only; never rewrite/delete.
- Opening commands (quote/complete/Trader/START) operator-run only.

## Job guards
- No stale refs (#628, #634 names never reused).
- Out-of-scope -> `docs/issues/632-noticed-but-not-touching.md`.
- No strategy code modified during planning.

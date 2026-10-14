# CONSTRAINTS — #573 (locked by Station II, enforced through Station V)

Branch: i573/unify-one-sided-stop-loss | Issue: #573

## Scope & File Boundaries
- Modify: `core_brain/config.py` (mode + renamed trigger vars + env),
  `core_brain/single_buy_saver.py` (`_route_pair`), `tests/test_dual_stop_loss.py`.
- Do NOT modify: the non-ladder lifecycle floor (`single_leg_lifecycle.transition`
  `hard_stop_bid`), grace/aged-out rules, `data/orders.db`, any live trading path.

## The shadow == live invariant (deliberate, not a regression)
- Shadow and live MUST apply the same stop decision; only order placement differs.
- A parity test asserts this. Do not "fix" a red parity test by diverging the paths.

## One mode per run (deliberate)
- Exactly one stop mode active per run (usd XOR pct). The old OR'd dual-trigger is
  replaced, not kept alongside. The % rule is RETAINED (runnable in isolation),
  NOT retired.

## Quality bar
- Zero regressions: `python -m pytest -q tests/test_dual_stop_loss.py
  tests/test_single_buy_saver.py` passes; new behavior has tests.
- Anti-cheat: no skipped/disabled tests, no deleted assertions, no linter suppression.
- No new external dependencies.
- Naming/typing (docs/agents/python-conventions.md): SCREAMING_SNAKE_CASE constants,
  type hints + docstrings on public surface, absolute repo-rooted imports.

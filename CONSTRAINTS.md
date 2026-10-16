# Constraints - #626 (purge instantaneous taker-ask re-gating on resting maker bids)

Branch: i626/purge-instantaneous-taker-ask-re-gating | Issue: #626

## Zero regressions

- Focused checks during build and after review fixes:
  `python -m pytest -q tests/test_trader_loop.py tests/test_cancel_attribution.py`
- Every changed behaviour needs a test that fails without the change (RED first).
- Full `python -m pytest -q` stays with GitHub CI. Never run it locally; never hand it
  to the operator.

## Anti-cheat

- Never skip/delete tests, weaken assertions, or add skip/xfail/noqa to reach green.
- Replacing the `CANCEL_REGATE_PAIR_COST` import with the literal
  `"regate_pair_cost"` in historical-row fixtures is allowed. Live-behavior tests stay.
- No `# type: ignore`, no `# noqa`.

## Production safety

- Never write to `data/orders.db`. Tests use their own stores.
- No live commands: no `quote`, `complete`, Trader loop, signer, dashboard START,
  or `order_manager` subcommand.

## Scope

- Touch ONLY: `core_brain/trader_loop.py`, `tests/test_trader_loop.py`,
  `tests/test_cancel_attribution.py`, `dashboard/static/strategy_explainer.html`.
- Do NOT touch: `core_brain/quotes.py` (`dynamic_pair_sum` entry gate),
  `core_brain/risk.py` (`hard_block`), `core_brain/single_leg_lifecycle.py`,
  `core_brain/shadow_pairs.py`, `core_brain/shadow_run.py`,
  `core_brain/cancel_report.py`, `core_brain/config.py`, `data/*.db`.
- `plan_orders` signature stays (`hedge_asks`, `hedge_held`, `dead_band`, `cfg`).

## Job guards

- No destructive git ops (reset, force-push, `checkout --`, stash) without operator OK.

# Constraints - #628 (atomic stop-loss exit)

Branch: i628/atomic-stop-loss-exit | Issue: #628

Re-locked for #628: the previous file still carried #627's scope (it merged to `main`
inside PR #633 and forbade `core_brain/single_leg_lifecycle.py`, which this plan must read).
Header and Scope below are #628's; every anti-cheat, production-safety and job-guard
line is kept verbatim.

## Zero regressions

- Focused checks during build and after review fixes:
  `python -m pytest -q tests/test_single_leg_lifecycle.py tests/test_single_buy_saver.py tests/test_trader_loop.py`
- Every changed behaviour needs a test that fails without the change (RED first).
- Full `python -m pytest -q` stays with GitHub CI. Never run it locally; never hand it
  to the operator.

## Anti-cheat

- Never skip/delete tests, weaken assertions, or add skip/xfail/noqa to reach green.
- The new entry gate allows the tie at the cap (issue wording `<=`); the existing
  `>=` arms in `risk.hard_block` and the legacy fresh-market branch keep their
  behavior byte-identical. Changing either to match is a separate issue.
- No `# type: ignore`, no `# noqa`.

## Production safety

- Never write to `data/orders.db`. Tests use their own stores.
- No live commands: no `quote`, `complete`, Trader loop, signer, dashboard START, or
  `order_manager` subcommand.
- No stop-threshold tuning: `ABSOLUTE_DROP` / `PERCENTAGE_DROP` knobs, tick size,
  `emergency_drawdown_pct`, and `max_pair_cost` semantics stay byte-identical.
- No taker-ask re-gating on resting quotes (purged in #626, closed).

## Scope

- Touch ONLY: `core_brain/single_buy_saver.py`, `core_brain/single_leg_lifecycle.py`,
  `core_brain/trader_loop.py`, `tests/test_single_buy_saver.py`,
  `tests/test_single_leg_lifecycle.py`, `tests/test_trader_loop.py`.
- Do NOT touch: `core_brain/risk.py`, `core_brain/order_registry.py`,
  `core_brain/order_manager.py`, `core_brain/config.py`, `core_brain/quotes.py`,
  `core_brain/shadow_run.py`, `core_brain/unhedged_stop_loss.py`, `data/*.db`.
- State machine stays: `DUAL_RESTING`, `PATIENT_WAIT`, `ESCALATED_HEDGE`,
  `HARD_STOP`, `PAIR_LOCKED`. No new state, no renamed action.

## Job guards

- No destructive git ops (reset, force-push, `checkout --`, stash) without operator OK.
- Base is synced: `git fetch origin` done 2026-10-16, 0 commits behind `origin/main`,
  tree clean on `i628/atomic-stop-loss-exit`.

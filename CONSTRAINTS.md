# Constraints - #627 (anchor entry pair-cost validation and passive single-leg break-even tracking)

Branch: i627/anchor-entry-pair-cost-and-passive-break-even | Issue: #627

Re-locked for #627: the previous file still carried #626's scope (it merged to `main`
inside PR #630 and forbade `core_brain/quotes.py`, which T1 of this plan must edit).
Header and Scope below are #627's; every anti-cheat, production-safety and job-guard
line is kept verbatim.

## Zero regressions

- Focused checks during build and after review fixes:
  `python -m pytest -q tests/test_quotes.py tests/test_registry_state.py tests/test_completable_pair_gate.py tests/test_live_quotes.py tests/test_trader_loop.py tests/test_order_manager.py`
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
- The break-even anchor is read-time derived telemetry: no schema change, no registry
  write, and nothing in execution, lifecycle or the dashboard may consume it.

## Scope

- Touch ONLY: `core_brain/quotes.py`, `core_brain/registry_state.py`,
  `tests/test_quotes.py` (new file, created here).
- Do NOT touch: `core_brain/risk.py`, `core_brain/order_registry.py`,
  `core_brain/order_manager.py`, `core_brain/trader_loop.py`,
  `core_brain/single_leg_lifecycle.py`, `core_brain/unhedged_stop_loss.py`,
  `core_brain/config.py`, `core_brain/cancel_report.py`, `core_brain/shadow_pairs.py`,
  `core_brain/shadow_run.py`, `dashboard/server.py`, `dashboard/static/*`, `data/*.db`.
- Signatures stay: `decide_quotes`, `_decide_quotes_from_mid`, `summarize_state`
  (read-only `mode=ro`), `FillRecord`, `OrderRecord`. Fills SELECT gains
  `f.recorded_ts` only; `ORDER BY` and `mode=ro` stay.

## Job guards

- No destructive git ops (reset, force-push, `checkout --`, stash) without operator OK.

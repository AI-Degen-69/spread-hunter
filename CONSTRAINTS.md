# Constraints - #619 (entry-relative stop-loss, retire static $0.15 stop)

Branch: i619/add-stop-loss-architecture-dynamic-modes-plus-entr | Issue: #619

- **Zero regressions:** `tests/test_dual_stop_loss.py` +
  `tests/test_single_leg_lifecycle.py` (acceptance pair) plus focused
  `test_single_buy_saver`, `test_hard_stop_fresh_exit`,
  `test_exit_price_realism`, `test_aged_out_rescue`, `test_dynamic_offset`,
  `test_shadow_tournament` must pass. Every new behavior needs a test that
  fails without the change (TDD RED first). Full suite stays with CI on push;
  never run it locally during build.
- **Anti-cheat:** never skip/disable tests, delete assertions, or suppress
  linters to reach green. `drop_cents` endpoints `0.01`/`0.90` and refusal
  cases are contract, not optional.
- **No new dependencies.** `Decimal(str(...))` math only (matches
  `max_profitable_hedge_bid`, lines 205-228); no epsilon.
- **#576 overlap checked 2026-10-16:** its dead-knob list
  (`stat_gate_alpha/beta`, experiment trio, `balance_hedge_sec`, `sim_only`,
  `scarcity_close_threshold`, `est_reward_pool_usd`,
  `float_mark_retention_days`) contains none of this issue's knobs — no
  stop-and-ask trigger.
- **Scope discipline:** files in the plan's untouched list are not opened for
  edits. Scope disputes go to the reviewer under the fix-size rule — never
  back to the builder, never silent expansion.
- **Production safety:** never write to or delete `data/orders.db`. No live
  trading commands at any station.

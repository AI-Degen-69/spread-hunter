# Constraints - #576 (remove settings that nothing in the bot reads)

Branch: feat/576-remove-unread-settings | Issue: #576

Replaces #639-scoped file wholesale (Step 0A.0: stale issue scope).

## Zero regressions
- Targeted test suites:
  `python -m pytest -q tests/test_config_for_display.py tests/test_stat_gate.py tests/test_market_selection_bars.py tests/test_statistical_validation_run.py`
  Full suite = CI gate on push.
- Retained fields MUST remain present, untampered, and with identical defaults:
  - `core_brain/config.py`: `stat_gate_deltas`, `stat_gate_threshold_pct`, `stat_gate_bankroll_pct`, `stat_gate_target_closes`, `single_buy_grace_sec`, `stranded_max_wait_sec`, `pairs_exit_window_sec`, `wide_book_trial`.
  - `scoring/config.py`: `select_min_top3_depth_usd`, `select_max_book_spread`.
- Strategy and execution invariants:
  - ZERO changes to active pricing, risk sizing, quoting logic, or venue interaction.
  - ZERO order placement; zero live trader calls; data/orders.db untouched.

## Deliberate contracts (not regressions)
- Exactly 10 unread fields are purged from `MakerConfig`:
  1. `float_mark_retention_days`
  2. `scarcity_close_threshold`
  3. `est_reward_pool_usd` (docs cleanup only; code already purged in #594)
  4. `stat_gate_alpha`
  5. `stat_gate_beta`
  6. `experiment_census_markets`
  7. `experiment_verdict_markets`
  8. `hedge_fillable_min_rate`
  9. `balance_hedge_sec` (confirmed no hardcoded timer exists; deleted)
  10. `sim_only`
- 24 extra unread candidate settings discovered during audit MUST NOT be deleted in this PR (parked in `docs/issues/576-noticed-but-not-touching.md` to prevent scope inflation).
- `return_pct_day` dict key in `scripts/filter_markets.py` is preserved for telemetry/dashboard stability; docstrings in `evaluate()` and `rank_score()` updated to reflect post-rebate spread-capture movement notional.

## Anti-cheat
- No skipped or disabled tests, no weakened assertions.
- No modifying existing tests to force green passes if a removed setting turns out to have an active reader.
- Guard test verifies deletion of the 10 fields while asserting presence of retained neighbors.

# Plan - #576: Remove the settings that nothing in the bot reads

Branch: feat/576-remove-unread-settings | Issue: #576
Size: Standard (3 files modified, 2 docs modified, focused test additions)
Task Type: [Backend/Logic] [Docs]

## Intake & Context
- Issue: #576 ([REFACTOR] Remove the settings that nothing in the bot reads)
- CodeRabbit Plan Intake:
  - Adopted: Deletion of the confirmed unread fields, guard test asserting absence of 10 fields, preserving retained neighbors (`stat_gate_deltas`, `stat_gate_threshold_pct`, `stat_gate_bankroll_pct`, `stat_gate_target_closes`, `single_buy_grace_sec`, `stranded_max_wait_sec`, `pairs_exit_window_sec`, `select_min_top3_depth_usd`, `select_max_book_spread`).
  - Discovered & Resolved: `est_reward_pool_usd` was already purged from code in #594 and tested in `test_rewards_purge.py`; only a prose reference in `docs/agents/strategy.md:81` remains.
  - Scope parking: 24 extra unread candidate settings are explicitly parked in `docs/issues/576-noticed-but-not-touching.md`.
  - Agent note 2026-10-17: Vocabulary clarification in `scripts/filter_markets.py` (`return_pct_day` represents post-purge spread-capture movement notional, not rebate yield).

---

## Tasks

### Task 1: Guard Test & Deletion of 10 Unread Fields
- **Size:** S
- **Domain:** [Backend/Logic]
- **Target Files:**
  - `tests/test_config_for_display.py`
  - `core_brain/config.py`
  - `scoring/config.py`
  - `docs/prompts/statistical-validation-run.md`
  - `docs/agents/strategy.md`
- **Description:**
  1. Add a guard test in `tests/test_config_for_display.py` asserting:
     - None of the 10 unread fields (`float_mark_retention_days`, `scarcity_close_threshold`, `est_reward_pool_usd`, `stat_gate_alpha`, `stat_gate_beta`, `experiment_census_markets`, `experiment_verdict_markets`, `hedge_fillable_min_rate`, `balance_hedge_sec`, `sim_only`) are declared on `core_brain.config.MakerConfig` or `scoring.config.MakerConfig`.
     - Retained fields (`stat_gate_deltas`, `stat_gate_threshold_pct`, `stat_gate_bankroll_pct`, `stat_gate_target_closes`, `single_buy_grace_sec`, `stranded_max_wait_sec`, `pairs_exit_window_sec`, `select_min_top3_depth_usd`, `select_max_book_spread`) are present.
  2. In `core_brain/config.py`, remove the unread fields and their orphaned comment blocks:
     - `float_mark_retention_days` (:170)
     - `scarcity_close_threshold` (:504) and update `scarcity_marginal_multiple` comment.
     - `stat_gate_alpha` (:977), `stat_gate_beta` (:978)
     - `experiment_census_markets` (:998), `experiment_verdict_markets` (:999), `hedge_fillable_min_rate` (:1000)
     - `balance_hedge_sec` (:1011)
     - `sim_only` (:1078)
  3. In `scoring/config.py`, remove the mirror unread fields and comment blocks.
  4. In `docs/prompts/statistical-validation-run.md:76`, keep starting numbers (60, 120) without naming them as MakerConfig settings.
  5. In `docs/agents/strategy.md:81`, remove `est_reward_pool_usd` from the reward-knob list.
- **Depends on:** None
- **Verification:**
  `python -m pytest -q tests/test_config_for_display.py tests/test_stat_gate.py tests/test_market_selection_bars.py tests/test_statistical_validation_run.py`

### Task 2: Align Post-Rebate Ranking Vocabulary
- **Size:** XS
- **Domain:** [Docs/Logic]
- **Target Files:**
  - `scripts/filter_markets.py`
- **Description:**
  1. Update docstrings for `rank_score()` and `evaluate()` in `scripts/filter_markets.py` to clarify that `return_pct_day` measures spread-capture movement notional post-rewards purge, not daily rebate yield.
  2. Ensure the dictionary key `return_pct_day` is preserved for telemetry and dashboard consumers without changing calculation formulas.
- **Depends on:** Task 1
- **Verification:**
  `python -m pytest -q tests/test_market_selection_bars.py`

### Task 3: Out-of-Scope Ledger & Final Gate Verification
- **Size:** XS
- **Domain:** [Docs/Verification]
- **Target Files:**
  - `docs/issues/576-noticed-but-not-touching.md`
- **Description:**
  1. Create `docs/issues/576-noticed-but-not-touching.md` cataloging the 24 discovered unread candidate settings (e.g. `profit_take_fee_per_share`, `capital_scarce`, `quote_both_sides`, `requote_interval_sec`), assigning owner "you" and when "when #571 or future refactor lands".
  2. Run the non-pytest hands-on python reflection check:
     `python -c "from core_brain.config import MakerConfig; import dataclasses; names = [f.name for f in dataclasses.fields(MakerConfig)]; assert 'sim_only' not in names; assert 'stat_gate_deltas' in names; print('Config verified')"`
- **Depends on:** Task 2
- **Verification:**
  - Targeted pytest suites all pass.
  - Zero git diff outside planned scope.

---

## Checkpoint Stops
- **Checkpoint 1 (after Task 1):** Guard test passes; config definitions cleanly purged; all targeted suites green.
- **Checkpoint 2 (after Task 3):** Full verification gate green; out-of-scope ledger recorded; ready for Station IV review.

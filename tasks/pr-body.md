Resolves #576

### Summary of Changes

1. **Purged 10 Unread Fields from `MakerConfig`:**
   Removed 10 fields and their orphaned comment blocks across `core_brain/config.py` and `scoring/config.py`:
   - `float_mark_retention_days`: Float-mark retention window.
   - `scarcity_close_threshold`: Negative close threshold; reworded `scarcity_marginal_multiple` comment.
   - `est_reward_pool_usd`: Cleaned surviving documentation reference in `docs/agents/strategy.md:81` (code was already purged in #594).
   - `stat_gate_alpha`, `stat_gate_beta`: Fixed statistical gate parameters.
   - `experiment_census_markets`, `experiment_verdict_markets`, `hedge_fillable_min_rate`: Experiment end criteria block; updated `docs/prompts/statistical-validation-run.md:76` to cite numbers without `MakerConfig` references.
   - `balance_hedge_sec`: Balance enforcement timer (investigation confirmed no hardcoded timer exists; active closeout uses `stranded_max_wait_sec` and `pairs_exit_window_sec`).
   - `sim_only`: Practice-mode flag.

| Field | `core_brain/config.py` | `scoring/config.py` | Status |
|---|---|---|---|
| `float_mark_retention_days` | Deleted | Deleted | Purged |
| `scarcity_close_threshold` | Deleted | Deleted | Purged |
| `est_reward_pool_usd` | Purged (#594) | Purged (#594) | Docs cleaned |
| `stat_gate_alpha` | Deleted | N/A | Purged |
| `stat_gate_beta` | Deleted | N/A | Purged |
| `experiment_census_markets` | Deleted | Deleted | Purged |
| `experiment_verdict_markets` | Deleted | Deleted | Purged |
| `hedge_fillable_min_rate` | Deleted | Deleted | Purged |
| `balance_hedge_sec` | Deleted | Deleted | Purged |
| `sim_only` | Deleted | Deleted | Purged |

2. **Guard Tests Added:**
   Added `test_unread_settings_purged_from_maker_configs()` and `test_retained_neighbors_still_declared()` in `tests/test_config_for_display.py` asserting that none of the 10 fields can be re-declared, and that retained neighbors (`stat_gate_deltas`, `stat_gate_threshold_pct`, `stat_gate_bankroll_pct`, `stat_gate_target_closes`, `stranded_max_wait_sec`, `pairs_exit_window_sec`, `select_min_top3_depth_usd`, `select_max_book_spread`) remain intact.

3. **Ranking Vocabulary Alignment (Post-Rebates):**
   Updated docstrings for `rank_score()` and `evaluate()` in `scripts/filter_markets.py` to clarify that `return_pct_day` represents post-purge spread-capture movement notional rather than a rebate yield, without altering calculation formulas or telemetry keys.

4. **Out-of-Scope Ledger:**
   Created `docs/issues/576-noticed-but-not-touching.md` documenting 24 extra unread candidate settings discovered during audit (e.g. `profit_take_fee_per_share`, `capital_scarce`, `quote_both_sides`, `requote_interval_sec`) with owner "you" and deferred to #571 / future refactors.

---

### Verification

- **Targeted Test Suites:**
  `python -m pytest -q tests/test_config_for_display.py tests/test_stat_gate.py tests/test_market_selection_bars.py tests/test_statistical_validation_run.py` (59 passed in 2.42s).
- **Reflection Check:**
  Confirmed reflectively in Python that none of the 10 fields exist on `MakerConfig` while active neighbors remain.

---

### How to verify (Hands-on operator steps)

1. Run the Python reflection check to verify `MakerConfig` fields:
   ```powershell
   python -c "from core_brain.config import MakerConfig; import dataclasses; names = [f.name for f in dataclasses.fields(MakerConfig)]; assert 'sim_only' not in names; assert 'balance_hedge_sec' not in names; assert 'stat_gate_deltas' in names; print('Config fields verified cleanly')"
   ```
   Confirm output displays `Config fields verified cleanly`.

2. Inspect read-only parameter export from dry calculations:
   ```powershell
   python -B scripts/stat_gate.py --dry-calc --sigma 0.05 --bankroll 100 --json
   ```
   Confirm the command executes without missing-field errors and outputs power table calculations.

3. Run a quick 1-minute rehearsal (no money spent, no credentials loaded):
   ```powershell
   python -m core_brain.shadow_run --minutes 1 --db data\rehearsal_576.db
   ```
   Confirm clean execution with zero configuration warnings.

# CONSTRAINTS — #654

Branch: migrate_canonical_config_names | Issue: #654 | Plan: `tasks/plan.md`

The bar this change is held to. Weakening any line to reach green is a failure,
not a fix.

## 1. Zero regressions
- Baseline: 597 passed, 0 failed, 0 skipped across the 15 focused test files
  (`test_quotes.py`, `test_order_manager.py`, `test_single_leg_lifecycle.py`,
  `test_trader_loop.py`, `test_completable_pair_gate.py`, `test_dynamic_offset.py`,
  `test_reward_offset_override.py`, `test_legacy_ask_quotes.py`,
  `test_stop_loss_config.py`, `test_dual_stop_loss.py`, `test_aged_out_rescue.py`,
  `test_single_buy_saver.py`, `test_config_single_source.py`,
  `test_exit_price_realism.py`, `test_shadow_pairs.py`).
  Target command:
  `python -m pytest tests/test_quotes.py tests/test_order_manager.py tests/test_single_leg_lifecycle.py tests/test_trader_loop.py -q`
  Baseline: 299 passed. All must pass without regression.
- Every new behaviour ships with a test that fails without the change.
- Zero threshold, math, or default value changes: pure rename and access migration.

## 2. Anti-cheat
- No `skip`, `xfail`, or deleted/weakened assertions.
- No `# type: ignore`, `# noqa`, or warnings silencer helpers added to production code.
- Warning guards in tests must filter specifically by scoped legacy name and engine
  file path; do not assert zero warnings globally because `dataclasses.replace`
  inherently warns from `dataclasses.py` when copying configs with legacy properties.

## 3. Dependencies & Frozen Surfaces
- No new external dependencies, no tooling changes.
- `core_brain/config.py`: DO NOT MODIFY. The `_LegacyProperty` descriptor,
  constructor wrapper, field registration, and defaults remain untouched.
- `core_brain/pair_scanner.py`, `core_brain/shadow_exec.py`, and `scoring/config.py`:
  DO NOT MODIFY.
- `_market_cfg` in `core_brain/trader_loop.py`: DO NOT MODIFY.
- `dashboard/server.py` and `dashboard/static/strategy_explainer.html`: DO NOT MODIFY.
- `pytest.ini` and `docs/ideas/variable-names-refactor.md`: DO NOT MODIFY.
- `data/orders.db`: DO NOT REWRITE OR DELETE.
- `tests/test_config_single_source.py`, `tests/test_config_for_display.py`,
  `tests/test_exit_price_realism.py`, `tests/test_shadow_pairs.py`,
  `tests/test_lifecycle_single_executor.py`, `tests/test_maker_queue_bar.py`:
  DO NOT MODIFY.
- `max_pair_cost_at_post` record fields and outgoing `max_pair_cost=` keywords:
  REMAIN UNCHANGED.

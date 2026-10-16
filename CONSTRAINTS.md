# Constraints — #603 (delete `_route_pair`, move coverage to unified lifecycle)

Branch: i603/refactor-delete-the-route-pair-exit-helper-that | Issue: #603

- **Zero regressions:** the 4-file acceptance suite
  (`test_single_buy_saver`, `test_refill_after_exit`, `test_exit_price_realism`,
  `test_grace_sweep_report`) plus focused `test_auto_pairs`,
  `test_aged_out_rescue`, `test_single_leg_lifecycle` must pass; every moved
  assertion needs a test that fails without the migrated setup. Full suite stays
  with CI on push.
- **No behavior change:** `manage_single_leg_positions`, executors, and all
  config fields stay untouched — a red migrated test means fix the test setup
  (timestamps, venue holdings, book switching), never lifecycle code.
- **Anti-cheat:** no skipping/disabling tests, no deleted assertions, no
  suppressing linters to reach green.
- **Dependencies:** none new. No file outside the 5 in-scope files
  (`core_brain/single_buy_saver.py`, `core_brain/shadow_run.py`,
  `core_brain/statistical_analytics.py`, the two test files) without approval.

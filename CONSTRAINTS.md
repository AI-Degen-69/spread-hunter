# CONSTRAINTS: Issue #681 - Purge surviving maker-rewards formulas and vocabulary

Branch: refactor/purge-rewards-filter-markets-681 | Issue: #681

## Quality Guardrails & Anti-Cheat
1. **Zero regressions on touched modules**:
   - `tests/test_rewards_purge.py`
   - `tests/test_unified_universe.py`
   - `tests/test_filter_markets_out_dir.py`
   - `tests/test_rank_score.py`
   - `tests/test_redesigned_filter_pipeline.py`
   - `tests/test_paired_depth.py`
2. **Anti-cheat**:
   - Strictly forbid disabling or skipping tests, deleting assertions, or suppressing linter warnings to force a pass.
   - Every changed behavior must be verified by targeted tests that fail without the change.
3. **Scope discipline**:
   - Touch only `scoring/rewards.py`, `scripts/filter_markets.py`, and the targeted test files.
   - Do NOT touch `core_brain/trader_loop.py` or `core_brain/config.py` (owned by separate issues).
   - Real taker fees (`fee_rate` in pair_scanner) are venue trading fees, NOT maker rebates, and must stay intact.
4. **Clean terminal contract**:
   - Terminal output from `python -m scripts.rank_markets` must never print maker reward terms (`income > $0/day`, `ret%/d`, `$/day`, `X spread / Y reward`, or reward pool income totals).
5. **No new dependencies**:
   - Zero new pip or external packages.

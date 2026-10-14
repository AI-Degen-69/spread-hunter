## Summary

Deletes the retired short-window ladder path (#325) end to end, per issue #589:

- **Deleted:** `core_brain/ladder.py`, the four `scripts/ladder_*.py` trial scripts, and the nine `tests/test_ladder_*.py` suites.
- **Engine:** the five `ladder_*` config fields and `validate_ladder` (`config.py`), `discover_ladder_series` (`markets.py`), `decide_ladder_quotes`/`route_quotes` (`quotes.py`), the `is_ladder_pair` import and bypass (`single_buy_saver.py`), and the ladder selection inside `_route_pair` (always `single_buy_exit` / `grace_expired` now).
- **Taxonomy:** `ladder_exit` in `close_methods.py` marked legacy with no current writer — the name, its rescue-bucket membership, and the string itself stay (old databases still contain these rows).
- **Survivors repaired:** the probe's `mean_ci90` / `_prop_ci90_diff` inlined into `tests/test_541_audit_goldens.py` (the `statistical_analytics` equivalents give different numbers, so the #541 pins need this formula); the #525 trigger-bid section moved off the ladder vehicle onto the generic path with the same coverage; fixture run-ids and db labels de-laddered.
- **Docs:** stale ladder-path comments reworded in `shadow_exec`, `shadow_run`, `trader_loop`, `statistical_analytics`; `first-run.md` loses the deleted rehearsal script as its example; the operator explainer keeps its record under a retirement banner.

The acceptance grep is the refined form from `tasks/plan.md`: the enumerated strategy symbols are gone repo-wide, while the order-book depth ladder (`snapshot_ladder`), risk-sizing ladder prose, and all `ladder_exit` taxonomy strings are deliberately preserved.

## Merge order

This branch edits `single_buy_saver.py`, `close_methods.py`, and `statistical_analytics.py`, which the #558–560 taxonomy chain also touches. **Land behind #558–560.** On a `close_methods.py` conflict, keep the taxonomy chain's version, then re-apply the legacy-writer note.

Closes #589

## Verification

- Targeted suites for every touched module: **470 passed** (`test_shadow_run`, `test_trader_loop`, `test_single_buy_saver`, `test_config_for_display`, `test_market_quote`, `test_refill_after_exit`, `test_exit_price_realism`, `test_close_method_taxonomy`, `test_541_audit_goldens`, `test_dashboard_run_switcher`, `test_empty_markets_routine`, `test_scan_state_shadow`, `test_live_state_language`, `test_docs_layout`).
- Acceptance grep: `ladder_mode|is_ladder_pair|discover_ladder_series|route_quotes|decide_ladder_quotes|validate_ladder|make_ladder_decide|ladder_pair_id` → zero hits across `core_brain scripts tests dashboard configs`.
- Dashboard smoke: `http://127.0.0.1:8799/` → 200; the served `data_analysis.js` carries the new producer string with zero ladder references.

@coderabbitai summary

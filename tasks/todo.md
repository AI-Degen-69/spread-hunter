# Todo — #589

Branch: i589/remove-short-window-ladder

- [x] T1 [M] [Code] — Delete `core_brain/ladder.py`; strip config fields + `validate_ladder`, `discover_ladder_series`, `route_quotes`/`decide_ladder_quotes`, `is_ladder_pair` import + bypass
- [x] T2 [M] [Code] — Strip ladder selection from `_route_pair`; always `single_buy_exit` / `grace_expired`
- [x] T3 [XS] [Code] — Mark `ladder_exit` writer legacy in `close_methods.py`; keep taxonomy strings
- [x] T4 [M] [Code] — Delete 4 scripts + 9 ladder suites; inline `mean_ci90`/`_prop_ci90_diff` into `test_541_audit_goldens.py`; rename ladder fixture labels
- [x] T5 [S] [Docs] — Comment-only edits + `first-run.md`, `data_inventory.md`, `data_analysis.js`, retirement notice on the explainer
- [x] T6 [XS] [Code] — Acceptance sweep: refined grep + issue's four suites + quote-lifecycle suites

Checkpoints: after T1+T2 (engine green), after T4 (all surviving suites green).

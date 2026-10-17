# Noticed but not touching — #572

Out-of-scope findings parked here during Station II planning. Each entry names
an owner and a when. Nothing below is licensed by #572.

| # | Finding | Owner | When |
|---|---------|-------|------|
| N1 | `max_spread_from_mid` names TWO different concepts: the engine score-window width `v` (core_brain default 0.15) and the venue rebate-qualification window (scoring default 0.045). A rename would end the confusion. | #571 (settings-file merge) or a dedicated rename issue | When the two config files are merged and the field can be renamed once |
| N2 | `scripts/filter_markets.py` snapshots `_CFG = _load_cfg()` and `MAX_BOOK_SPREAD` once at import and never rebuilds; env values landing after import are unseen. The derived property flows through automatically, but the import-time snapshot pattern itself is tech debt. | Future config-hygiene issue | When config loading is reworked, not in a behavior-preserving consolidation |
| N3 | `scoring/selector.py` is documented "deliberately pure" yet its `0.0205` literal defaults are a third copy of the picker bar. The plan wires the default from config at module load; a `None`-means-default signature would preserve purity more fully but changes every caller. | Future selector-cleanup issue | If the selector gains more config-derived defaults and the import starts to smell |
| N4 | `HUNTER_MAX_SPREAD_CENTS` general-path parsing in `core_brain/config.py:1329-1346` feeds the score window, not the book ceiling; its naming invites confusion with the spread ceiling owned here. | #571 or a dedicated env-var cleanup | When env-var naming is rationalized |
| N5 | `dashboard/static/strategy_explainer.html` hardcodes the `0.06` (6c) display value; after consolidation it should read the derived terms. Display-only copy. | Any dashboard-touching issue | Next dashboard copy pass |

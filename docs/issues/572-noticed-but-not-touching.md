# Noticed but not touching — #572

Scope guard for #572 (2026-10-17, Station II planning; resolved 2026-10-18, Station VI closeout).
Out-of-scope findings parked here during Station II planning and resolved during Station VI closeout.

| ID | candidate | discovering station | evidence (path:line) | owner | when / date | status | resolution |
|---|---|---|---|---|---|---|---|
| N1 | `max_spread_from_mid` names TWO different concepts: engine score-window width `v` (0.15) vs venue rebate window (0.045) | Station II | `core_brain/config.py:172`, `scoring/config.py:111` | #571 | When config files are merged (#571) | duplicate | #571 |
| N2 | `scripts/filter_markets.py` snapshots `_CFG = _load_cfg()` and `MAX_BOOK_SPREAD` once at import; env changes unseen | Station II | `scripts/filter_markets.py:22` | config-infra | Future config rework | dismissed | Import-time snapshot pattern is tech debt; property already flows through dynamically |
| N3 | `scoring/selector.py` defaults are third copy of picker bar; wires from config at load | Station II | `scoring/selector.py:21` | selector-team | Future selector pass | dismissed | Wires default from config at module load; None-default signature changes callers |
| N4 | `HUNTER_MAX_SPREAD_CENTS` general-path parsing feeds score window, not book ceiling | Station II | `core_brain/config.py:1340` | #571 | Env-var rationalization pass | duplicate | #571 |
| N5 | `dashboard/static/strategy_explainer.html` hardcodes `0.06` (6c) display value | Station II | `dashboard/static/strategy_explainer.html:120` | dashboard | Next dashboard copy pass | dismissed | Display-only copy; will read derived terms in future UI pass |
| N6 | `statistical_validation_run/run.py:223` config snapshot drops property key | Station II | `statistical_validation_run/run.py:223` | stat-validation | 2026-10-18 (#572) | dismissed | Resolved in #572: key explicitly preserved in config_snapshot.json and verified by test |


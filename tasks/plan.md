# Plan — Issue #705: Remove hardcoded literal suffixes from parameter cards

Branch: i705/dashboard-parameter-card-suffix | Issue: #705

## Size / Type
- Tier: Small (one file `dashboard/server.py` + one test file `tests/test_dashboard_server.py`), one rule.
- Task type: Code (UX/copy correctness on the dashboard surface).

## Intake note (CodeRabbit plan)
- Adopted: exact-match regression test pattern (`monkeypatch.setenv` 20¢ → `$0.20`), default field left untouched, frontend untouched, safety exclusions.
- Rejected (operator directive): single-card scope. The ` (15¢)` suffix is one instance of a 15-instance "restated default in another unit" class at lines 4319, 4387, 4684, 4701, 4805, 4841, 4858, 4984, 5001, 5020, 5076, 5093, 5180, 5214, 5250 of `dashboard/server.py`. One rule, one file, fixed once as a class.
- Resolution note: line 4754 `(50% of spread)` and the `(Telemetry Only)` card carry information the bare number does not — kept (operator classification rule).
- CodeRabbit claim repinned: it found "no test asserting the `(15¢)` format" — that is stale/wrong. `tests/test_dashboard_server.py:3259` pins `1800s (30m)`; repinned in this change.

## Tasks
1. Strip the 15 literal suffixes that restate a default in another unit from
   `dashboard/server.py` `get_parameters()`. Keep `(50% of spread)` and
   `(Telemetry Only)`. Size S. Verified by the focused test run in task 3.
2. Repin `tests/test_dashboard_server.py:3259` to `"1800s"` with a comment naming
   why the old `1800s (30m)` pin was wrong; add regression test
   `test_max_spread_from_mid_card_shows_configured_value_exactly`
   (env `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID=20` → raw 0.20, value exactly
   `"$0.20"`, default `"$0.15"`). Size XS.
3. Run focused checks: `python -m pytest -q tests/test_dashboard_server.py`.
   Rerun after review fixes. Size XS.

Depends on: nothing (all three sequential, one change set).

## Out of scope
- `docs/agents/git-workflow.md` doc drift (`<type>/<slug>` branch prose, stale
  `@coderabbitai summary` placeholder) — recorded, not edited here.
- `strategy_explainer.html` `$0.15 (15¢)` copy — describes the shipped default,
  outside the card-render class.

# Checklist — #748 One category label across the Orders & Trades tabs

Branch: `i748/consistent-category-label-orders-trades` | Issue: #748

## T1 — Tag-aware resolver (`core_brain/market_meta.py`)
- [x] Build `CATEGORY_TAG_RANK` at import from `_SPORT_RULES`, `_ESPORTS_GAME_RULES` + the top-level venue list
- [x] Add `category_from_tags(row)` (highest rank, first tag wins a tie, malformed tags ignored)
- [x] Add `_category_from_feed_label(label)` (recognised labels only)
- [x] Rewrite the category precedence: venue label → tags → classifier → `UNCATEGORIZED`
- [x] Return `event_title` in the meta dict
- [x] Delete the `series_title` / `market_group` / `_first_tag_label` category sources
- [x] `python -m pytest -q tests/test_market_meta.py`

## T2 — Ask gamma for tags (`scripts/filter_markets.py`)
- [x] `include_tag: "true"` on the `/markets` page params
- [x] `include_tag: "true"` on the live-events params
- [x] Dedupe the feed row's `tags` (market + event) preserving order
- [x] `python -m pytest -q tests/test_unified_universe.py tests/test_live_event_discovery.py`

## T3 — One chip everywhere (`dashboard/static/app.js`)
- [x] Add `categoryPill(m)`; single template, escapes, muted for `Uncategorized`
- [x] Orders meta line: bare `stitch-meta-category` span → `categoryPill`
- [x] Active Markets: chip always rendered
- [x] Open Positions: chip + secondary line is event/market title only
- [x] Closed Trades: drop `categoryShort`, use `categoryPill`
- [x] Export `marketCategory`, `categoryPill`, the four `stitch*Rows`
- [x] `python -m pytest -q tests/test_stitch_active_markets.py`

## T4 — Resolver tests (`tests/test_market_meta.py`)
- [x] Rewrite the old-precedence assertions (series, group, first tag)
- [x] Most specific tag wins (`Sports, NBA, Games, Basketball, NBA Tip-Off` → `Basketball`)
- [x] Non-category tags never become a label (`NBA Tip-Off`, `UCL Matchday`, `Republican Party`, `Aaron Ford (D)`, `Data 2`, `Spread -3.5`)
- [x] Tags beat the classifier; classifier fills in when tags are empty
- [x] `event_title` is returned; malformed `tags` never raise
- [x] `python -m pytest -q tests/test_market_meta.py`

## T5 — Fetch-param tests
- [x] `tests/test_unified_universe.py`: the `/markets` request carries `include_tag`
- [x] `tests/test_live_event_discovery.py`: the live-events request carries `include_tag`
- [x] `python -m pytest -q tests/test_unified_universe.py tests/test_live_event_discovery.py`

## T6 — Stitch-surface coverage
- [x] `tests/js/orders_trades_harness.cjs`: `surface: "stitch"` dispatches to `stitch*Rows`
- [x] `tests/test_orders_trades_table.py`: one `stitch-category-pill` per market row, four views
- [x] no `stitch-meta-category`, no `categoryShort` in the rendered output
- [x] Open Positions has no second line repeating the category
- [x] `python -m pytest -q tests/test_market_meta.py tests/test_orders_trades_table.py tests/test_stitch_active_markets.py`

## Wrap-up
- [ ] Full changed-file review (the five files + two test files) against `CONSTRAINTS.md`
- [ ] `tasks/pr-body.md` written; PR opened; one CodeRabbit round; CI green; merge; #748 closed

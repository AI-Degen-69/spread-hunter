# Constraints — #748 One category label across the Orders & Trades tabs

Branch: `i748/consistent-category-label-orders-trades` | Issue: #748

## Hard scope

- **Display only.** The category never gates admission, ranking, sizing or risk
  (`docs/agents/strategy.md:39-42`). No file under `strategy/` or `scoring/`
  changes, and `scripts/filter_markets.py` changes only in what it **asks the
  venue for** and what it **writes into the row** — never in a filter, a score or
  a gate.
- **Five files may change:** `core_brain/market_meta.py`,
  `scripts/filter_markets.py`, `dashboard/static/app.js`,
  `tests/test_market_meta.py`, `tests/js/orders_trades_harness.cjs`,
  `tests/test_orders_trades_table.py`, plus two existing test files that already
  own the fetch seams (`tests/test_unified_universe.py`,
  `tests/test_live_event_discovery.py`). A change outside this list is out of
  scope — stop and split it.
- **Never touch `data/orders.db`.** It is the production registry. Read only.
- **No venue contact from this work.** No `quote`, no `complete`, no Trader
  loop, no `order_manager` subcommand. The tag vocabulary was sampled live once
  during planning, from `%TEMP%`, and nothing of it is committed.
- **The legacy table is out of scope.** `dashboard/static/prototype.js:572` adds
  `proto-body` on boot, so the dashboard paints the stitch renderers
  (`app.js:8919-8937`). `marketCell`'s `caption-muted` category line and the
  legacy rows keep their current markup.

## Zero regressions

- Targeted checks covering the touched files, run by the agent during
  development and after review fixes:
  `python -m pytest -q tests/test_market_meta.py tests/test_orders_trades_table.py tests/test_unified_universe.py tests/test_live_event_discovery.py tests/test_stitch_active_markets.py tests/test_kpi.py`.
  The full `python -m pytest -q` suite is the GitHub CI merge gate; do not repeat
  it locally.
- **`tests/test_stitch_active_markets.py` stays green untouched.** It pins the
  Active Markets column structure; the category chip's contents may change, its
  presence and class may not.
- New behaviour needs a test that fails without the change. Every acceptance row
  in `SPEC.md` names the test that fails when its behaviour is absent.

## Frozen contracts

- `resolve_market_meta(cid, closes, quotes, root=...)` keeps its signature and
  its returned keys. `event_title` is **added** to the returned dict (the
  Positions secondary line needs a real title); no key is removed or renamed.
- `kpi.by_market[cid]` is `{**meta, ...}` (`core_brain/kpi.py:1616`), so an added
  meta key reaches the frontend without a KPI change. A KPI change is out of
  scope.
- `marketCategory(m)` keeps its name, its single argument and its
  `Uncategorized` fallback — it is already exported and already called by every
  renderer.
- Feed rows keep `series_title`, `market_group`, `event_title`, `tags`,
  `venue_category` and `category` with their current meanings. Only the
  **resolver's use** of them changes.
- `stitch-category-pill` is the only category markup. No new CSS class; the chip
  and its muted `Uncategorized` styling already exist in
  `dashboard/static/prototype.css:1313-1323`.

## Anti-cheat

- No skipped or xfailed test, no deleted assertion, no widened tolerance. An
  existing assertion that encodes the **old** precedence (series/group becoming
  the category) is **rewritten** to the new contract, and the plan lists each one
  — rewriting an assertion that guards a behaviour still wanted is not allowed.
- No new external dependency. `requests` and `node` are already used.

## Performance / resource ceilings

- The resolver stays pure and dict-only: no I/O, no network, no new import beyond
  what `market_meta.py` already imports at module scope.
- The tag ranking is O(tags) per market with a fixed vocabulary map built once at
  import — no per-row regex compilation.
- `include_tag=true` adds no page: it widens each existing `/markets` and
  `/events` response, not the page count.

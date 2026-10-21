# Plan — #748 One category label across the Orders & Trades tabs

Branch: `i748/consistent-category-label-orders-trades` | Issue: #748

**Tier: Standard** (7 files, no schema change, one architectural decision: the tag
ranking vocabulary). **Type: Code + UX/Copy** (a data-resolution fix whose visible
outcome is one consistent chip).

## Intake note (CodeRabbit plan comment, read once)

- **Adopted:** its precedence chain (venue label → venue tags → classifier →
  `Uncategorized`), its file map, and its observation that the node harness only
  drives the legacy rows.
- **Rejected:** its suggestion of a general "category confidence" layer and a new
  resolver module — the resolver already exists in `market_meta.py`; a second
  module would split one precedence across two files.
- **Resolved `[UNVERIFIED]` items** (checked against live code during planning):
  - *"`marketCategory` does not exist yet"* → **false**: it exists at
    `dashboard/static/app.js:7050` and all four renderers already call it
    (8063, 8222, 8437, 8798). The defect is markup/placement plus the data,
    not a missing helper.
  - *"The stitch path is untested"* → **true**, and the reason is now known:
    `ordersTradesRows` (`app.js:7748`) always takes the legacy branch. The
    `proto-body` class that selects the stitch renderers is added at runtime by
    `dashboard/static/prototype.js:572`, so the harness must call the stitch
    renderers directly.
  - *"Gamma needs `include_tag=true`"* → **true**, re-verified live: 400 markets
    sampled, tags absent without the flag and present with it; nested event
    markets inherit the parent event's tags.
  - *Issue line numbers are stale by ~60–90 lines* (e.g. `marketCategory` cited
    at `:7150`, actually `:7050`). Paths and symbols were right; every location
    below was re-derived live.
- **Tag vocabulary decision (evidence):** a live sample of 400 top-volume markets
  returned **249 distinct labels**, dominated by non-categories (`Recurring`,
  `Hide From New`, `Earn 3.25%`, `NBA Tip-Off`, `UCL`, team and candidate
  names). A flat allow-list of venue labels is therefore not maintainable and
  "first tag" is wrong. The ranking reuses the repo's own display rules
  (`_SPORT_RULES`, `_ESPORTS_GAME_RULES`, `_POLITICS_RE`) as the vocabulary.

## Resolved open questions

| Question | Answer (from code / live venue) |
| --- | --- |
| Where does a tag reach the frontend? | Feed row `tags` (`filter_markets.py:923`) → `resolve_market_meta` → `meta["category"]` → `kpi.by_market[cid] = {**meta, ...}` (`kpi.py:1616`) → `app.js` `marketCategory(m)`. No server aggregation step in between. |
| Does the Positions secondary line have a real title? | Not today: `resolve_market_meta` reads `event_title` (`:302`) but never returns it, and KPI drops it. Add `"event_title"` to the returned dict — `{**meta}` carries it to the page with no KPI change. |
| Is the legacy markup still painted? | No. `prototype.js:572` adds `proto-body` on boot; `app.js:8919-8937` then paints the stitch renderers. Legacy stays untouched. |
| One label or a two-token label? | One. `stitch-category-pill` carries the most specific label (`Basketball`, not `Sports / Basketball`). |

## Interface contracts

### Python — `core_brain/market_meta.py`

```python
CATEGORY_TAG_RANK: dict[str, int]        # canonical label -> 3 (sport / named game),
                                         # 2 (Politics, E-Sports, Geopolitics),
                                         # 1 (Sports, Crypto, Culture, Economy,
                                         #    Tech, Weather, World)

def category_from_tags(row: dict) -> Optional[str]
    # Highest-ranked canonical label among row["tags"]; first one wins a tie;
    # None when no tag is a category. Non-list / non-str tags are ignored.

def _category_from_feed_label(label: str) -> Optional[str]
    # A venue label accepted only when it is recognised: it canonicalises to a
    # sport/game/Politics label, or it is in CATEGORY_TAG_RANK, or it already
    # equals a canonical label ("E-Sports"). Anything else -> None.

def resolve_market_meta(cid, closes=None, quotes=None, *, root=None) -> dict
    # category precedence (rewritten):
    #   1. _category_from_feed_label(venue_category or category)
    #   2. category_from_tags(row)
    #   3. classify_display_category(title, event_title, slug)
    #   4. UNCATEGORIZED
    # new key: "event_title" (str, "" when the feed has none)
```

`series_title`, `market_group` and `_first_tag_label` stop being category
sources; `_first_tag_label` is deleted once a grep confirms nothing else reads it.

### Python — `scripts/filter_markets.py`

- `/markets` params (`:826-832`) gain `"include_tag": "true"`.
- `LIVE_EVENTS` params (`:1030-1035`) gain `"include_tag": "true"`.
- Row `tags` dedupes while keeping order: `list(dict.fromkeys(...))` over the
  market + event labels (`:923`). No other row field changes.

### JavaScript — `dashboard/static/app.js`

```js
function marketCategory(m)          // unchanged: m.category || 'Uncategorized'
function categoryPill(m)            // NEW: <span class="stitch-category-pill">…</span>
                                    // always rendered, always escaped, muted for
                                    // 'Uncategorized'
```

- Orders (`:8063`): bare `stitch-meta-category` span → `categoryPill(m)` in the
  meta line; `Paired Arb` keeps `stitch-meta-tag`.
- Active Markets (`:8222`): → `categoryPill(m)` (chip no longer suppressed).
- Open Positions (`:8437`): → `categoryPill(m)`; the secondary line at `:8494`
  drops the `m.category` fallback and renders only `m.event_title` (or
  `m.subtitle`), hidden when empty or identical to the category label.
- Closed Trades (`:8735-8736`, `:8798`): drop `categoryShort`, → `categoryPill(m)`.
- `module.exports` gains `marketCategory`, `categoryPill`,
  `stitchOpenOrdersRows`, `stitchActiveMarketsRows`, `stitchPositionsRows`,
  `stitchClosedTradesRows`.

### Test seam — `tests/js/orders_trades_harness.cjs`

`argv[2]` gains an optional `"surface": "stitch"`, which dispatches `view` to the
exported `stitch*Rows` instead of `ordersTradesRows`. Default stays legacy, so
every existing assertion keeps its meaning.

## Tasks

### T1 — `[Backend/Logic]` Tag-aware resolver · **M** · `core_brain/market_meta.py`
Depends on: none.
Build the rank map from the existing rule tables at import, add
`category_from_tags`, rewrite the category precedence, add `event_title` to the
output, delete the series/group/first-tag sources.
**Verify:** `python -m pytest -q tests/test_market_meta.py`.

### T2 — `[Backend/Logic]` Ask gamma for tags · **S** · `scripts/filter_markets.py`
Depends on: none (parallel with T1).
Add `include_tag=true` to both request param dicts; dedupe the row's tags.
**Verify:** `python -m pytest -q tests/test_unified_universe.py tests/test_live_event_discovery.py`.

### T3 — `[Design/UI]` One chip in all four tabs · **M** · `dashboard/static/app.js`
Depends on: T1 (needs the `category` + `event_title` semantics), T2 (tags reach it).
Add `categoryPill`, route the four stitch renderers through it, drop
`categoryShort` and the Positions category fallback, export the stitch renderers.
**Verify:** render one view by hand through the harness, then
`python -m pytest -q tests/test_stitch_active_markets.py`.

### T4 — `[Backend/Logic]` Resolver tests · **M** · `tests/test_market_meta.py`
Depends on: T1.
Rewrite the assertions that encode the old precedence (`series_title`,
`market_group`, first-tag → category) and add: most-specific tag wins;
`NBA Tip-Off` / `UCL Matchday` / `Republican Party` / `Data 2` / `Spread -3.5`
never appear as a label; tags beat the classifier; the classifier still fills in
when tags are empty; `event_title` is returned.
**Verify:** same command as T1.

### T5 — `[Backend/Logic]` Fetch-param tests · **S** · `tests/test_unified_universe.py`, `tests/test_live_event_discovery.py`
Depends on: T2.
Assert `include_tag=true` on the `/markets` page request and on the live-events
request, using the fake sessions those files already own.
**Verify:** same command as T2.

### T6 — `[Design/UI]` Stitch-surface harness coverage · **M** · `tests/js/orders_trades_harness.cjs`, `tests/test_orders_trades_table.py`
Depends on: T3.
Add the `surface` switch to the harness and four assertions in the Python test:
per-view row count unchanged; exactly one `stitch-category-pill` per market row;
no `stitch-meta-category` and no `categoryShort` output; Open Positions carries
no second line repeating the category.
**Verify:** `python -m pytest -q tests/test_orders_trades_table.py`.

**Checkpoints:** after T1+T2 — resolver and feed agree (probe one real market
through `resolve_market_meta`). After T4+T5 — targeted Python suites green.
After T6 — `python -m pytest -q tests/test_market_meta.py tests/test_orders_trades_table.py`
green, then the operator-facing check below.

## How to verify (operator)

1. `.\scripts\spread-hunter-menu.ps1 start`, open `http://127.0.0.1:8799`.
2. Orders & Trades → click through **Orders**, **Active Markets**, **Open
   Positions**, **Closed Trades**. Every row shows exactly one small chip under
   the market name, in the same place, reading a real category (`Basketball`,
   `Politics`, `Crypto`, `Uncategorized`) — never `NBA Tip-Off`,
   `Republican Party`, `Aaron Ford (D)`, `Data 2` or `Spread -3.5`.
3. On **Open Positions**, the second line under a row's chip is the event or
   market title, not the category word again.

## Improvement proposal (adopted — simplification / edge-case hardening)

Evidence, `core_brain/market_meta.py:317-334`: the category is built by an `or`
chain that mixes four different kinds of text — `venue_category`, `category`,
`series_title`, `market_group`, first tag — into one `_canonical_feed_label(...)`
call. That chain is the bug: it cannot tell a category from a group name, so any
fix that only appends tags leaves `Republican Party` reachable whenever tags are
absent. **Adopted:** replace the chain with an ordered list of *typed* candidates
(venue label, ranked tags, classifier), so a non-category string has no path to
the label. Cost: one function, no new file.

## Noticed but not touching

- `dashboard/static/app.js:8919-8937` duplicates the four-way view dispatch in
  both the stitch and legacy branches; a table-driven dispatch would halve it.
  Out of scope — the issue is the label, not the dispatch.
- `_first_tag_label`'s "first tag wins" is wrong for every caller; only the
  category path uses it, so it is removed rather than generalised.
- The legacy `marketCell` caption still prints a bare-text category. Unreachable
  in the browser (`proto-body`), left alone deliberately (see `CONSTRAINTS.md`).

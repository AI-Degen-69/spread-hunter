# SPEC — One category label across the Orders & Trades tabs (#748)

Branch: `i748/consistent-category-label-orders-trades` | Issue: #748

## Goal

Every market row in the four Orders & Trades tabs (Orders, Active Markets, Open
Positions, Closed Trades) shows **exactly one** category label, painted by the same
`stitch-category-pill` chip, in the same place, with the full label — and that label
is a real venue category, never a series, group or outcome name.

## Where the label comes from

One resolver (`core_brain.market_meta.resolve_market_meta`), one precedence:

1. The venue's own explicit label — feed `venue_category`, then `category` —
   canonicalised through the existing display rules (`ATP Tour` → `Tennis`,
   `CS2` → `Counter-Strike 2`). Only recognised categories are accepted.
2. The market's own venue **tags**, most specific category-level tag wins
   (Basketball / Tennis / Politics / Crypto / Economy …). Non-category tags
   (`NBA Tip-Off`, `UCL Matchday`, `Republican Party`, `Aaron Ford (D)`, `Data 2`,
   `Spread -3.5`) never become the label.
3. The existing keyword classifier over title / event title / slug.
4. `Uncategorized`.

`series_title`, `market_group` and arbitrary first-tag values stop being category
sources. They keep every other use they have in the feed row and in the Open
Positions secondary line.

## Why it is broken today (verified against live code and the live venue)

- `scripts/filter_markets.py` never asks gamma for tags (`include_tag=true`), so
  `tags` is empty on feed rows and the resolver never reaches step 2.
- `resolve_market_meta` therefore falls through `series_title` → `market_group`,
  which is where `Republican Party` / `Data 2` come from.
- `dashboard/static/app.js:7050` `marketCategory()` already exists and every
  renderer already calls it — the four tabs disagree about *markup and
  placement*, not about the resolver call: Orders prints bare text
  (`stitch-meta-category`), the other three print a chip, Open Positions also
  duplicates the word in its secondary line (`classification` falls back to
  `m.category`), and Closed Trades still computes a dead 4-character
  `categoryShort`.
- The node harness drives the **legacy** rows (`app.ordersTradesRows`), while the
  dashboard paints the stitch renderers (`prototype.js:572` adds `proto-body`,
  `app.js:8919-8937` dispatches on it) — so the path the operator actually sees
  has no test.

## Acceptance criteria

Mapped 1:1 to the issue's checklist; each one is asserted by a named test.

| # | Criterion | Test |
| --- | --- | --- |
| A1 | Every row of all four tabs shows exactly one `stitch-category-pill` and no `stitch-meta-category`/bare text | `tests/test_orders_trades_table.py` (stitch surface, 4 views) |
| A2 | Resolution order: venue label → most specific venue tag → classifier → `Uncategorized` | `tests/test_market_meta.py` |
| A3 | No series/group/outcome name can become a label | `tests/test_market_meta.py` (negative vocabulary) |
| A4 | Open Positions secondary line is an event/market title only | `tests/test_orders_trades_table.py` |
| A5 | The gamma scan requests tags on `/markets` and `/events` | `tests/test_unified_universe.py`, `tests/test_live_event_discovery.py` |
| A6 | The harness covers the four stitch renderers | `tests/js/orders_trades_harness.cjs` + `test_orders_trades_table.py` |
| A7 | Display-only: no selection/ranking/sizing/risk change | no diff outside the four files below |

## Edge cases

- A market whose only tags are non-category (`Senate`, `United States`,
  `Midterms`) still resolves: `Politics` from the classifier or a politics tag;
  if neither, `Uncategorized`. Never `Senate`.
- A sports market tagged `Sports, NBA, Games, Basketball, NBA Tip-Off` resolves to
  `Basketball`, not `Sports` and not `NBA Tip-Off` (rank, not tag order).
- A market with no feed row (order outlived its market) keeps `Uncategorized`.
- A tag list arriving as a non-list (`"nope"`, `{}`) must not raise.

## Out of scope

- The legacy (non-`proto-body`) table markup. It is unreachable in the browser —
  `prototype.js` adds `proto-body` on boot — so it stays as-is; only the stitch
  renderers change.
- Any change to market selection, ranking, sizing, risk caps or universe gates.
- Localising or re-labelling the venue's category vocabulary.

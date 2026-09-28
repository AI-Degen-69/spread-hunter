# Plan: Issue #295 — Orders & Trades markets show Uncategorized instead of venue category

Branch: i295/orders-trades-markets-show-uncategorized-instead-of-venue | Issue: #295

Size: **Large** — 5 product files across filter → core → frontend plus a new
shared module, in 3 phases. Task type: **Code** (backend + display).

## Open questions — resolved from code (no operator input needed)
- **Q1, live Gamma shape:** CodeRabbit's bounded live sample already answered it —
  market-level `category`/`categorySlug`/`tags` come back null and
  `events[].series` is a list of objects with usable titles (`NFL 2026`,
  `League of Legends`). Confirmed against the current parse at
  `scripts/filter_markets.py:511`, which matches that shape. Adopted: defensive
  market→event extraction, series/group preserved, tags collected.
- **Q2, category vocabulary:** no authoritative Polymarket list in docs or code.
  Adopted: venue strings verbatim; keyword fallback maps only to `E-Sports` and
  `Politics` (seeded from, but not importing, `scoring/selector.py` lists).

## CodeRabbit plan intake (read once; echo ignored)
- **Adopted:** 3-phase skeleton (filter extraction → shared `market_meta.py`
  resolver → four-view display); precedence
  category → series → group → first tag → keyword → Uncategorized; universe
  fallback for departed markets; Market-cell captions instead of new columns;
  all cited file paths/symbols verified against live code.
- **Rejected:** over-split task lists merged into 4 atomic tasks below; no new
  persistent metadata archive (universe rows suffice).
- **[UNVERIFIED] at plan time, now verified:** `events[].series` list-of-objects
  shape (matches `filter_markets.py:511`); `market_universe.json` rows carry the
  eligible-builder fields (writer at `filter_markets.py:1349-1380` persists
  scored rows, so a `tags` copy rides along).

## Dependency graph
T2 Depends on: T1 (resolver reads the `tags` field T1 persists).
T3 Depends on: T2 (frontend renders the `category` T2 resolves).
T4 Depends on: T1, T2, T3 (final parity + verification).

## Tasks

### T1 [Backend/Logic] Gamma label extraction + persistence in the filter (M) [x]
Target files: `scripts/filter_markets.py`, `tests/test_unified_universe.py`.
- Guarded first-event / first-series helpers in `gamma_universe` (tolerate
  list, JSON-string list, null, dict).
- Venue category = first non-blank of market `category`/`categorySlug`, event
  `category`/`categorySlug`; collect `tags` labels (market then event).
- Copy `tags` into eligible rows (=> `markets.json` + `market_universe.json`);
  selection/ranking untouched.
- Tests: null-category + `series[0].title="League of Legends"`; event
  category + tags verbatim; malformed shapes never raise.
- Helper skill: test-driven-development. Depends on: —.
- Verify: `python -m pytest -q tests/test_unified_universe.py` (RED first, then GREEN).

### T2 [Backend/Logic] Shared `core_brain/market_meta.py` resolver + wiring (L) [x]
Target files: `core_brain/market_meta.py` (new), `core_brain/kpi.py`,
`core_brain/registry_state.py`, `tests/test_market_meta.py` (new),
`tests/test_registry_state.py`.
- Resolver loads `markets.json` then `market_universe.json` rows (cid-indexed,
  feed wins, read errors ignored); precedence category → series → group →
  first tag → keyword → `Uncategorized`; `UNCATEGORIZED` moves here, re-exported
  from `kpi`; shared title/slug/URL derivation.
- Pure word-boundary classifier over title/event-title/normalized-slug:
  E-Sports first, Politics second; display-only comment, no selector imports.
- `_resolve_market_meta` and `_market_identity` delegate; KPI keeps
  close-then-quote slug discovery; registry identity gains `category`.
- Tests: both operator slugs with no feed row; venue-verbatim override case;
  series-verbatim case; tag-only case; universe-only case; all-blank case;
  KPI/registry parity per cid.
- Helper skill: test-driven-development. Depends on: T1.
- Verify: `python -m pytest -q tests/test_market_meta.py tests/test_registry_state.py tests/test_portfolio_overview.py`
  (`test_portfolio_overview.py:259-277` unchanged).

### T3 [Backend/Logic + Display] Same category in all four views (M) [x]
Target files: `dashboard/static/app.js`, `tests/test_orders_trades_table.py`,
`tests/js/orders_trades_harness.cjs` (only if `state.pairs` forwarding needed).
- `marketCategory(m)` helper (non-blank or `Uncategorized`); Active Markets
  Category cell uses it instead of `m.category || '--'`.
- Opt-in category caption in `marketCell()` for Open Orders + Positions
  (inside the row-spanning Market cell); opt-in param on shared
  `marketRowPairHtml()`, passed only from `closedTradesRows()`.
- Unmatched Open Orders fallback: `by_market[cid]` → `state.pairs` identity
  (by `pair_id`, else `condition_id`) → truncated cid.
- Tests: operator fixtures (`E-Sports`, `Politics`) + `MLB`/`NBA` verbatim
  cases in all four views; column/rowspan invariants; unmatched-order case;
  Python-level `kpi.report()` feed-miss test.
- Helper skill: test-driven-development. Depends on: T2.
- Verify: `python -m pytest -q tests/test_orders_trades_table.py`.

### T4 [Backend/Logic] Final parity gate + closeout hygiene (S) [x]
Target files: none (verification only).
- Rerun the full focused selection T1–T39922 together; confirm the STATUS-header
  vocabulary test passes unmodified; confirm no filter CLI was run and no
  runtime files were published.
- Helper skill: incremental-implementation. Depends on: T1, T2, T3.

## Checkpoints
- After T1: filter rows carry `tags`; universe + markets rows verified by tests.
- After T2: `by_market` and pair identity agree on category for feed, universe,
  and slug-only markets.
- After T3: dashboard shows the same non-empty category in all four views.

## Improvement proposal (adopted by default)
Fix the adjacent `market_type` copy bug on the exact lines T1 already touches:
eligible-row builder reads `"market_type": m.get("marketType") or m.get("type")`
from the candidate dict, but the candidate stores the flattened key
`"market_type"` — so graduated rows always persist `market_type: ""`. One-token
fix (`m.get("market_type")`), covered by a T1 test. Drop it only on explicit
operator rejection.

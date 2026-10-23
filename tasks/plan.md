# Plan — Issue #783: Orders & Trades Category column + slimmer rows + Active Markets cleanup

Branch: i783/orders-trades-category-column-slimmer-rows | Issue: #783
(Worktree branch in use: `freebuff/ii-plan-issue-783-18e9e92c-8c4e-4aa6-a66e-608f9d699fef`;
the canonical branch above is what Stations III–V push and open the PR from.)
Size tier: **Standard** — one repeated column pattern across 4 views + stylesheet; no backend, API, or dependency change.
Task type: **[Design/UI] + [Backend/Logic]** (stitch JS renderers) — verification: automated pytest + hands-on browser check.

## CodeRabbit plan intake (read once, mapped here so later stations never re-read it)
- **Adopted:** CATEGORY right after Market in all four tabs; sort key 2 for Active Markets /
  Positions / Closed Trades, new key 8 for Orders (+ `OT_TEXT_COLUMNS['open-orders']`);
  shared `stitch-category-cell` class with the pill moved out of the Market cell;
  Active Markets Edge header/cell removal incl. `pairCost`/`edge`/`edgePct` math and sort cases 5–6
  (case 8 untouched); volume-only 24H VOLUME cell; colspan updates (Orders 5→6, Positions 8→9,
  Closed 6→7 incl. expand row, Markets stays 7); header-order + sort-direction assertions;
  TDD phase split (tests RED first, then renderers GREEN).
- **Rejected:** manual `?v=` bump in `index.html` — unnecessary churn. Evidence:
  `dashboard/server.py` — `_STATIC_ASSET_RE = re.compile(r"/static/([A-Za-z0-9_.\-]+)(?:\?v=[A-Za-z0-9]+)?")`
  with "Rewrite /static/ refs to content-hash ?v= so browsers refetch on change",
  and `tests/test_dashboard_server.py` — "`/` rewrites ?v= to the asset bytes: no author bump to remember."
- **[UNVERIFIED] resolved from code:** key 2 already returns `marketCategory` in the Active Markets
  (app.js:8185) and Closed Trades (app.js:8728) switches and is already in their text sets;
  key 8 is free in the Orders switch (cases 0,1,2,3,4,5,7) and key 2 is free in the Positions switch
  (cases 0,1,3,4,5,6,7,8,9, text set already contains 2); classic keys 5/6 belong to the classic
  path only; CSS baselines confirmed (`td` 14px vertical at prototype.css:803,
  `.stitch-market-cell` 14px at :962, title line-height 1.35 at :989, meta margin-top 4px at :1008,
  `th` 12px at :800); harness has no `stitchHead` (returns legacy `head` only).
- **Carried default:** existing Market header labels (`MARKET & CATEGORY`, `MARKET & CLASSIFICATION`)
  stay unchanged — a rename is out of scope and no acceptance criterion asks for it.

## Open questions (from the issue — resolved with issue defaults, no operator ask)
1. Column placement → immediately right of Market (recommended default). Locked in SPEC.md.
2. Slimming strength → cell vertical padding ~8px, title line-height ~1.25, meta margin-top ~2px (default).
3. Edge math → deleted with the cell (default; nothing else in this table consumes it).
Note: the issue carries a 🔄 Duplicate label, but no duplicate of #783 was found among open issues
(#784 Pair Cost column, #785 state-contradiction fix, #786 per-sport icons are adjacent, distinct work).
Left for Station I triage; planning is unaffected.

## Domain skills (all verified present)
`frontend-ui-engineering` + `frontend-design` (column + row-rhythm work),
`api-and-interface-design` (sort-key/column-list contract below),
`incremental-implementation` (task slicing), `constraint-driven-development` (CONSTRAINTS.md).
Skipped `type-design-analyzer` (persona exists): no new domain model — the contract is existing
integer sort keys + one CSS class. Skipped `code-explorer`: execution paths were traced directly
(app.js regions read verbatim above).

## Interface contract (locked before business logic)
- New shared class `stitch-category-cell` (`white-space: nowrap`, middle alignment); pill moves there.
- Column order + sort keys + colspans per SPEC.md table; no key renumbered/reused.
- `OT_TEXT_COLUMNS['open-orders']` gains 8 (keys 1, 2 kept; other views' sets unchanged).

## Improvement proposal (adopt-by-default, evidence above)
Drop the manual `?v=` bump step entirely — the server already fingerprints static assets per request,
so hand-editing version strings is churn with zero browser effect.

## Tasks

### T1 — Harness + failing tests first (RED) [x] [size M, Backend/Logic]
**Depends on:** — (first).
- `tests/js/orders_trades_harness.cjs`: add `stitchHead` (from the matching `stitch*HeadHtml`
  when `surface === 'stitch'`); leave `columns`/`head` untouched.
- `tests/test_orders_trades_table.py`: per-view pill assertions (one pill per row, inside the new
  Category cell, none inside `stitch-market-cell`); keep Uncategorized + no-duplicate-Politics checks;
  one header/sort test per view via `stitchHead` (CATEGORY button right after Market; key sorts category order).
  Classic assertions untouched.
- `tests/test_stitch_active_markets.py`: headers Market, CATEGORY, YES PRICE, NO PRICE, 24H VOLUME,
  STATUS, LAST UPDATED; drop `+0.0c`/`6.9d` checks; negative EDGE/`Nd` checks; positive `$152k` check;
  keep `colspan="7"`.
- `tests/test_stitch_open_orders.py`: six headers with CATEGORY second (Actions not sortable);
  empty colspan 6; key-8 grouping check; `otDefaultDir('open-orders', 8) == 'asc'`.
- `tests/test_stitch_positions_marks.py`: CATEGORY header check; keep CURRENT PRICE + `data-cell` checks.
- `tests/test_stitch_closed_trades.py`: empty state + `stitch-expand-row` both `colspan="7"`.
- `tests/test_sidebar_prototype.py`: one CSS check — body/`market-cell` vertical padding ≤ 10px, `th` keeps 12px.
- **Verify:** focused suites fail exactly on the new/changed assertions; everything else still passes.

### T2 — Four stitch renderers (GREEN) [x] [size L, Backend/Logic]
**Depends on:** T1.
- `dashboard/static/app.js`: per SPEC.md contract — add `{label:'CATEGORY',…}` after each Market entry;
  new `<td class="stitch-category-cell">` with `categoryPill(...)`; remove pill from each Market cell
  (drop the div if left empty); Active Markets: remove EDGE header/cell, `pairCost`/`edge`/`edgePct`
  math, sort cases 5–6, horizon sub-line (keep `fmtCompactUSD(m.volume_24h)` + case 8);
  Orders: new `case 8` via `orderGroupMarket(...)` market; Positions: new `case 2` reusing the row's
  `category`; colspans 6 / 9 / 7 (+ expand row); `OT_TEXT_COLUMNS['open-orders']` += 8.
- **Verify:** T1 suites turn green.

### T3 — Row rhythm in stylesheet (GREEN) [size S, Design/UI]
**Depends on:** T2.
- `dashboard/static/prototype.css`: body `td` + `.stitch-market-cell` vertical padding `14px→8px`;
  `.stitch-market-title` line-height `1.35→~1.25`; `.stitch-market-meta` margin-top `4px→~2px`;
  new `.stitch-category-cell` rule; `th` padding untouched; no `nth-child`/`td:first-child`.
- **Verify:** sidebar-prototype suite green; visual check in T4.

### T4 — Focused checks + hands-on surface pass [size S, Design/UI]
**Depends on:** T3.
- Run `python -m pytest tests/test_orders_trades_table.py tests/test_sidebar_prototype.py -q`
  and `python -m pytest tests/test_stitch_active_markets.py tests/test_stitch_open_orders.py
  tests/test_stitch_positions_marks.py tests/test_stitch_closed_trades.py -q` — all green.
- Hands-on (demo DB only, never START / BACK TO LIVE):
  `python -m dashboard.server --db data/demo_orders.db --host 127.0.0.1 --port 8799` →
  http://127.0.0.1:8799 Dashboard → Orders & Trades → four tabs show CATEGORY after Market;
  two clicks sort A→Z then Z→A; no EDGE / no `Nd` line; shorter rows; full-width Closed ledger row.

**Checkpoint after T1:** RED confirmed 2026-10-23 — 11 new/changed checks fail in
test_orders_trades_table (chip x4, head x4, positions-sort, orders-sort, first-click-asc),
7 fail across the four stitch suites + 1 sidebar CSS check; all other tests green.
**Checkpoint after T3:** all six focused suites green, ready for T4 surface pass.

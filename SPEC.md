# SPEC: Issue #311 - Aged-out one-sided legs are never revisited

## Goal
A one-sided leg that passes `pairs_exit_window_sec` (900 s) without being closed
is no longer invisible. A separate, market-end-aware arm discovers it, reads the
venue's own state for that market, and sells (or completes) the naked leg before
the market ends — instead of the leg sitting unmanaged until settlement pays out
whatever the outcome is. On shadow-01 all three `shadow_settlement` closes aged
out this way; the worst held 2.8 shares for 10 452 s (2 h 54 m, ~7x the window)
and lost $1.54, and the other two won — an unmanaged coin-flip with no risk cap.

## Acceptance criteria (from issue)
- [ ] A one-sided leg older than `pairs_exit_window_sec` is sold (or completed) before its market end, not at settlement
- [ ] An unreadable/unknown market end leaves the leg naked and retries; no invented deadline, no blind close
- [ ] Legs inside the 900 s window follow the unchanged route order
- [ ] The `aged_out` flag in `scripts/rescue_exit_report.py` goes to zero across a fresh rehearsal on the new build
- [ ] Focused test RED before / GREEN after: an aged-past-window leg with a known end is exited before it; the same leg with an unreadable end is not

## Scope
### In scope
- `aged_out_verdict(...)` — the pure, fail-closed decision (no clock, no registry, no network)
- `rescue_aged_out_legs(...)` — discovery by the window's complement, the existing close-coverage guard, `complete_pair` under `max_pair_cost` else `exit_single_buy(reason="aged_out_rescue")`
- `fetch_open_market_state(...)` + an additive `end_ts` field on `MarketEndState` in `core_brain/market_resolution.py`
- Two new config knobs (`enable_aged_out_rescue`, `aged_out_rescue_lead_sec`)
- Wiring in the live loop (`order_manager`) and the shadow sweep (`shadow_run`), with an injectable market-state seam
- Report: the rescue reason printed explicitly, plus the aged-out settlement count in the summary

### Out of scope
- Raising or redefining `pairs_exit_window_sec`; any change to `should_exit()`, the drift thresholds, grace defaults, the route order, `max_pair_cost` or the dynamic risk caps
- Backfilling the existing shadow-01/02/03 stores (they predate `closes.reason`)
- Changing `filter_markets.py`, the ranker, or `fetch_market_end_state`'s `closed=true` semantics; any live execution or new dependency

## Interface contracts
- `aged_out_verdict(*, last_fill_ms, window_ms, now_s, end_ts, lead_sec, venue_closed, venue_accepting) -> tuple[str, str]`
  — verdicts `not_aged_out | end_unknown | venue_closed | awaiting_lead | due`
- `fetch_open_market_state(gamma_host, condition_id, *, timeout=10.0, now_ts=None, urlopen=...) -> MarketEndState | None`
  — unfiltered gamma read; `None` = not in the venue's open listing; `unreachable=True` = failed read
- `MarketEndState.end_ts: Optional[float] = None` — filled by `parse_end_state` from the epoch it already computes
- `rescue_aged_out_legs(client, registry, cfg, *, live=True, now=None, venue_positions=None, market_state_fn=None, gamma_host=...) -> list[dict]`
  — per-pair isolation; actions `aged_out_rescue | awaiting_lead | end_unknown | venue_closed | balanced | error`
- Close reason vocabulary gains `aged_out_rescue`

## Edge cases (measured, not assumed)
- The `closed=true` gamma query returns **zero rows** for a still-open market (`condition_ids=0xd65042fa…`), so the resolution read cannot serve this deadline; the unfiltered query returns `closed=False`, `acceptingOrders=True`, `endDate`, `gameStartTime`
- Sports `endDate` may already be past while the venue still accepts orders (kickoff case from #312; the probe market shows a week-out `endDate` with `gameStartTime` hours past) — treated as the closing phase
- A market the venue no longer lists as open: no action, settlement owns it
- A pair that still completes under `max_pair_cost`: completed, never dumped at the bid
- An undated fill (no `venue_ts`): left alone by both arms

# SPEC: Issue #294 - Click-to-sort the Orders & Trades tables

## Goal
Every column header in the dashboard's Orders & Trades table becomes a sort
control: one click sorts the view by that column, a second click flips the
direction, and exactly one header shows which direction is active — so the
operator can rank the book by queue depth, pair cost, age or P&L instead of
reading rows in whatever order the builder produced.

## Acceptance criteria (from issue)
- [ ] Any `<th>` in `#orders-trades-table` sorts the visible rows by that column in all four `OT_VIEWS`; a second click flips; one indicator at a time
- [ ] Sorting re-orders the backing groups before row pairing, so pair rows stay adjacent, `rowspan` cells stay on `ot-pair-start`, and a CLOSED TRADES main row keeps its expanded sub-row beneath it
- [ ] Numeric columns sort from the underlying value (`$1,000.00` > `$95.00`, 9 < 10 shares, `1.5d` > `10.0d`); `Market` sorts by `localeCompare` on the title
- [ ] `--` ranks last in both directions
- [ ] Sort state is per view and survives tab switching and the 2s poll re-render
- [ ] With no header clicked, every view's row order is byte-identical to today
- [ ] The STATUS column keeps its `title` vocabulary inside the `<th>` opening tag; `test_active_markets_status_header_explains_the_vocabulary` passes unmodified
- [ ] Keyboard operable (real `<button type="button">`), `aria-sort` on the sorted `<th>`, arrow not the only signal
- [ ] `styles.css` gives the sortable header hover and `:focus-visible` using existing DESIGN.md tokens
- [ ] Added tests are RED against untouched `app.js`, GREEN after; existing tests stay green

## Scope
### In scope
- Per-view sort state `{col, dir}` in memory, threaded from `renderOrdersTrades` (`app.js:4432`) through `ordersTradesRows` (`:4425`) into the four builders
- Sort specs parallel to `OT_COLUMNS` (`:3748-3762`), one accessor per column reading the same raw values the builders use
- A sort control in every `<th>` from `otHeadHtml` (`:4037-4049`), with a delegated click listener on the persistent `#orders-trades-head`
- Export of new pure helpers via `module.exports` (`:5424-5431`) and pass-through of the sort input in `tests/js/orders_trades_harness.cjs`
- `dashboard/static/styles.css` sortable-header affordances

### Out of scope
- Any new endpoint, backend KPI change, or persisted sort storage
- The Data & Markets table, the other dashboard tables, or `OT_COLUMNS` label text
- Any change to the money path (`core_brain/`), the venue, or live execution

## Interface contracts (as shipped)
- `otHeadHtml(view, sort = null) -> string` — unchanged output when `sort` is null; the sorted `<th>` gains `aria-sort` and an indicator; the first `<th>` keeps `class="ot-market-head"`; the STATUS `<th>` keeps its `title` attribute. The direction is announced **once**, on the `<th>`; the arrow is `aria-hidden`.
- `ordersTradesRows(view, kpi, state, sort = null) -> string` — each builder sorts its backing groups/entries when `sort` is given, before mapping to HTML.
- `otSortGroups(groups, sort, valueOf)` — stable re-ordering; `valueOf(group, col, dir)` reads the underlying value per view. Each builder owns its own accessor switch, so a column's value sits next to the cell that renders it.
- `otCompare(a, b, dir)` — unmeasured (`null`/`undefined`) ranks last in both directions.
- `otDefaultDir(view, col)` / `otIsTextColumn(view, col)` — text columns start ascending, everything else descending.
- `otToggleSort(view, col)` / `otActiveSort(view)` — per-view in-memory state; `col` is validated with `Number.isInteger`, so a garbage index cannot silently disable sorting.
- Harness input gains `sort` (`{col, dir}`) and `toggle` (a list of clicks to replay).

## Defaults (per issue, unless the operator says otherwise)
- First click: text columns (`Market`, `Category`, `Leg`, `Status`, `Hedge`) ascending; every other column descending.
- Sort state is per view and in memory only.

## Evidence notes (verified against the tree, 2026-09-28)
- Issue line numbers are stale: `otHeadHtml` is 4037 (not 4009), `renderOrdersTrades` 4432 (not 4403), `module.exports` 5408-5431 (not 5392-5414). The issue's `:4448-4463` and `:4415-4418` anchors map to `initOrdersTradesTabs` 4477-4492 and the `closed-trades` expansion re-render at 4444-4447.
- Baseline: `python -m pytest -q tests/test_orders_trades_table.py` → 62 passed. Node v24.14.1 present, so the harness is not skipped.
- `aria-sort`, `data-sort` and `sortable` appear 0 times in `app.js`, confirming no partial sort work exists.
- `wireMarketRowExpansion` (`:4588`) is re-wired per render for the closed-trades body, so a delegated header listener on the persistent `<thead>` is the correct pattern (the `<thead>` element itself is never replaced).

## CodeRabbit plan intake (costed once)
- **Adopted:** sort the backing groups before row pairing (its central constraint); in-memory-only per-view state; delegated listener on the persistent `<thead>`; native `<button type="button">`; every column sortable with fixed first-click direction; additive columns sort by group sum, measurement columns by the extreme leg in sort direction.
- **Rejected:** its file/line anchors (stale, see above). Its suggestion to thread sort state through each builder separately is folded into one spec table rather than per-builder accessors, which is the same behavior in one place.
- **UNVERIFIED:** none left. Every seam it cited was re-checked in the tree.

# SPEC: Issue #291 - Shadow-03 depth-bar trial on its own feed

## Goal
A third parallel shadow rehearsal (shadow-03) trials a loosened top-3
bid-depth bar ($250 instead of $500) on its own ranker feed and output area,
measuring forward on live books whether depth-rejected markets pay — without
contaminating the shadow-01/02 baselines.

## Acceptance criteria (from issue)
- [ ] shadow-03 loop quotes trial-depth markets from its own feed while the 01/02 feed bytes are unchanged
- [ ] Menu R lists 01/02/03 and port 8803 serves the 03 store
- [ ] New feed-override tests fail without the change and pass with it
- [ ] python -m pytest -q tests/test_trial_readiness.py

## Scope
### In scope
- Ranker `--out-dir` (all RUN-anchored artifacts) + loop forwarding of `--out-dir` / `--trial-depth`
- `_market_specs(path=None)` + shadow `--markets-path` (precedence: injected fn → flag → default)
- Menu `shadow-trial` non-destructive launch, trial manifest (`data/<store>.trial.json`, absolute paths), manifest-driven Menu R resume
- `docs/agents/architecture.md` trial-feeds note

### Out of scope (per issue + CodeRabbit plan)
- Shipped $500 bar stays untouched; no 01/02 baseline behavior change; no live execution
- No volume trial; no ladder work (#49)
- Dashboard graduated-market/KPI panels keep reading the shared feed (port 8803 serves the 03 store)

## Interface contracts
- `filter_markets --out-dir <dir=RUN>`; `filter_loop --out-dir <dir> --trial-depth <usd>` (CLI trial wins over `HUNTER_DEPTH_TRIAL_USD`; absent flags → byte-identical command)
- `_market_specs(max_markets, path=None) -> list[8-field dict]`; `shadow_run --markets-path <file=None>`
- Trial manifest `{trial_depth_usd: 250, ranker_out_dir: <abs>, markets_path: <abs>}` next to the store; mirrored in `runtime/shadow-session-<run-id>.json`; `*.trial.json` never matches `NN_shadow_*.db` discovery

## Edge cases
- Invalid initial feed fails loudly; failed refresh keeps the last list
- No-manifest resume is byte-for-byte the current path (01/02 unaffected)
- Trial screener never registered as the global `filter` entry in `runtime/processes.json`
- While 01/02 live: no fresh start (menu 4), no stop without a run ID

# SPEC: Issue #295 — Orders & Trades markets show Uncategorized instead of venue category

## Goal
Every graduated market shows its real venue category in all four Orders & Trades
views (Active Markets, Open Orders, Positions, Closed Trades) — the venue label
verbatim when Gamma publishes one, a deterministic E-Sports/Politics keyword
fallback when it does not — instead of `Uncategorized` / `--`.

## Acceptance criteria (from issue)
- [ ] `Lol Kcb Wd 2026 09 26`-shaped markets resolve to an E-Sports label and
  `Will Luiz Incio Lula Da Silva Win The 2026 Brazilian Presidential Election`-shaped
  markets resolve to Politics when venue metadata is empty
- [ ] Markets with real venue category/tags show the venue label verbatim (no keyword override)
- [ ] Active Markets, Open Orders, Positions, and Closed Trades all show the same
  non-empty category for the same market (kpi + registry_state unified)
- [ ] New regression test pins both operator examples plus the venue-label-verbatim case
- [ ] Verification: `python -m pytest -q tests/test_orders_trades_table.py`

## Scope
### In scope
- Gamma category/tag extraction in the filter (`gamma_universe` + eligible rows)
- Category persistence in graduated `runtime/markets.json` rows and `market_universe.json` rows
- One shared resolver (`core_brain/market_meta.py`) serving `kpi.py` and `registry_state.py`
- Frontend fallback rendering (Active Markets cell + Market-cell captions elsewhere)
- Tests pinning the two operator examples

### Out of scope (per issue)
- Changing market selection/ranking by category
- Backfilling `data/orders.db` history
- New dashboard filter UI

## Interface contracts
- `core_brain/market_meta.py::resolve_market_meta(cid, closes, quotes) -> dict`
  with keys `condition_id, title, slug, url, category, days_to_resolve, min_size,
  volume_24h, source`; `category` never blank (falls back to `UNCATEGORIZED`)
- `classify_display_category(title, event_title, slug) -> str | None`
  pure function; returns `E-Sports`, `Politics`, or `None`
- `UNCATEGORIZED` lives in `market_meta` and is re-exported from `kpi`
- Category precedence: feed `venue_category` (market→event display label) →
  `category` → `series_title` → `market_group` → first `tags` label →
  keyword fallback → `Uncategorized`
- Gate separation (review round): the identity gate keeps reading market-level
  `category` only; `venue_category` is display-only and never feeds selection
- Eligible/universe rows gain a `tags: list[str]` field; no ranking input changes

## Edge cases
- Malformed Gamma shapes (events as dict/string/empty, series missing) never raise;
  fields stay blank and the keyword fallback still applies
- Slug-only markets (left the top-20 feed, absent from universe) classify from
  title/slug alone
- Venue label always wins over keywords (`Crypto` row with election title stays `Crypto`)
- Open Orders with no KPI entry falls back to the `state.pairs` market identity,
  then truncated condition id; category caption shows `Uncategorized`

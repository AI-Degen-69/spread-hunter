Branch: i744/route-in-play-sports-into-live-lane | Issue: #744

# Plan — #744 Route in-play sports into the live lane and make its numbers honest

## Tier / type

**Large** — cross-cutting: the ranker's live fetch, every evaluator exit, both
artifact writers, the funnel reader, the dashboard, 4 test suites and one doc.
No new dependency, no DB schema change; `runtime/pipeline.json` gains a block.
**Types:** [Backend/Logic] (primary) + [Design/UI] (lane numbers) + [Debug] (the
reported misclassification). Verification modes: pytest for the ranker/funnel,
node harness for the rendered lane.

## Spec (embedded — no SPEC.md for a single issue)

Problem: the live lane is a dashboard-side boolean guess over rows that already
lost their live evidence, and the lane's numbers are global counts over
lane-filtered cards. A venue-declared in-play market can be screened, graduated
and quoted as REGULAR while the live lane reports 0.

Goal: one lane decision per row, derived from the venue's live declaration,
attached to every row the ranker emits; membership re-derived every cycle;
lane-scoped counts in the snapshot so each lane's numbers describe that lane;
paginated live-set fetch.

Acceptance criteria are the issue's six checkboxes, refined where the code says
they are otherwise unreachable (see "Acceptance refinements").

Out of scope (from issue, unchanged): gate thresholds, the volatility-exemption
rule, `bot_pos_price_max_pair_cost_usd`, quoting/sizing/execution, the trader
loop, dashboard visual redesign, rewriting existing artifacts.

## Locked decisions (from the operator — do not re-open)

1. The live lane = **venue-declared in-play only**. A match that starts while
   being quoted must move lanes on the next filter cycle; it is never labelled
   regular while in-play.
2. Lane lag ≤ one filter cycle (~10 min). No dashboard-side venue fetch, no new
   background service.
3. "Priority" means **rank order** (`sort_eligible`). The
   `core_brain/market_feed.py` gap (execution cannot see a live quote) is
   recorded, not acted on.

## CodeRabbit plan intake (read once; later stations must not re-read the comment)

- **Adopted:** paginate the live fetch (page 100, advance by rows returned, stop
  on a short page, page cap + `live_truncated`, later-page failure keeps earlier
  pages + `live_error`); build a declaration map keyed by `condition_id` BEFORE
  the moneyline/two-token/volume checks; a pure `market_lane(row)` in
  `scoring/selector.py` imported by both ranker and reader; merge stamps the lane
  from this cycle's declarations on every output row; lane on every `evaluate`
  exit; extend `_live_evidence`; per-lane counts; kpi passthrough + lane and
  display fields on graduated rows; dashboard reads `lane` + lane numbers,
  explicit zeros, old-snapshot note, `lanes` in the fingerprint; tests in the 4
  named suites + `test_redesigned_filter_pipeline.py`; doc update.
- **Rejected (with reason):**
  - *`n_by_lane` on each rejection entry* → replaced by a per-lane `rejections`
    list of the **existing entry shape** (`cause, n, would_fund, traps,
    examples`). Same numbers, no new key, no dashboard-side re-grouping of two
    counters, and `test_redesigned_filter_pipeline.py:560`'s exact-keys assertion
    stays true instead of being widened.
  - *A `_venue_live` row field* → unnecessary. `merge` stamps `lane` from the
    declaration map; a declared-only row needs no other flag, so no new
    flag-shaped field can leak into a gate.
  - *"Ended vetoes live" inside `market_lane`* → the branch cannot fire:
    declarations are built only from events where `live and not ended`
    (`filter_markets.py:1069-1070`), and the universe is rebuilt from scratch
    each cycle, so no declared row can carry an ended event.
  - *"Update the source-string checks in `tests/test_dashboard_server.py` that
    refer to the old helper"* → **no such checks exist** (grep for
    `isLiveMarket` in that file: zero hits). Nothing to update.
  - *"Use the first live copy for both replaced and appended rows"* → both
    copies describe the same `condition_id` with the same event fields; the
    change is behaviourally invisible, so it is not made (noted below).
- **Resolved `[UNVERIFIED]` during intake:**
  - `filter_markets.py:2902-2905` is no longer an identity restamp — it is
    `_stamp_trial_identity(out, universe)` at `2905`, and it uses
    `row.setdefault(...)` (`1936-1944`), so it cannot drop `lane`. **No change
    needed there.**
  - `core_brain/kpi.py` may import `scoring.selector` — precedent exists
    (`core_brain/risk.py:397`, `core_brain/shadow_exec.py:621`).
  - `tests/test_dashboard_server.py` has no old-helper references (above).
  - `server/fleet_dash.py` (cited by an in-code comment at
    `filter_markets.py:2489`) does not exist; `pipeline.json` has exactly one
    reader, `core_brain/kpi.py`.
  - The 2026-10-21 artifacts confirm the report: `counts.scored=137`,
    `discovery.live.live_main_lines=5`, `raw.spread[0]` carries no live field,
    `markets.json[0]` (`cs2-mgc-mglz-2026-10-21`) carries `event_live: true`.

## Open questions — resolved from code (no operator input needed)

1. Pre-kickoff sports in the live lane? **No** — in-play only (locked). But the
   lane decision is re-derived at merge from the current declaration, so a
   pre-kickoff row that kicks off is stamped live on the next cycle.
2. Lag budget? **One cycle** — satisfied by doing the re-derivation at merge,
   which runs once per rank (`main` → `merge_live_event_markets`, `2875`).
3. Does priority reach execution? **No** — `sort_eligible` only
   (`filter_markets.py:567-573`); `GraduatedMarket` stays untouched.

## Acceptance refinements (criterion that cannot reach zero as written)

- "a test drives one row through each exit (identity refusal, movement refusal,
  book refusal, eligible, picked) and asserts the lane field is present and
  correct on all five" — *picked* is not an `evaluate` exit; it is a slice of
  the eligible list (`_select_picked`, `1954-1961`) and is projected by `_row()`
  (`2562-2572`). So the test asserts: lane on the four `evaluate` exits **and**
  on the `pipeline.json` `final` + `picked` projections. Five assertions stay.
- "the exercised suites pass" — the named command lists four files plus
  `test_redesigned_filter_pipeline.py`; that is the local targeted bar. The full
  suite is the CI gate (see CONSTRAINTS.md).

## Interfaces (locked before any business logic)

**`scoring/selector.py` — new pure function, next to `is_sports_or_esports`:**

```python
def market_lane(row: Mapping) -> str:  # "live" | "regular"
```

Precedence: (1) `row["lane"]` when it is exactly `"live"` or `"regular"` — a
row stamped this cycle outranks any stale flag; (2) any strictly-`True` of
`_event_live`, `event_live`, `_live_event`, `live_event`, `volatility_exempt` →
`"live"` (this is what classifies a `markets.json` row written before this
change, e.g. the WTA case); (3) otherwise `"regular"`. Period/score text is
never read. **No gate, floor, exemption, horizon or ranking call may use it.**

**Row contract (producer → artifact):** `lane` on every row emitted by
`evaluate` (5 exits), on every `market_universe.json` row (full row), on every
`markets.json` row (picked), and on `pipeline.json`'s `raw.spread` preview rows,
`rejections[*].examples[*]`, `final[*]`, `picked[*]`. Values: `"live"`/`"regular"`
only — never unknown, because the ranker always decides.

**`runtime/pipeline.json` — new block (additive; `counts` and `rejections` keep
their exact shape):**

```json
"lanes": {
  "live":    {"counts": { ...same keys as counts... },
              "rejections": [ {"cause","n","would_fund","traps","examples"} ]},
  "regular": { ... }
}
```

Per-lane `counts` keys: `funded` (0, legacy), `spread_universe` (= that lane's
candidate count in the scanned universe = the honest "to evaluate"), `attempted`,
`scored`, `dropped_no_verdict`, `rejected`, `eligible`, `picked`. Invariants:
`scored = rejected + eligible`, `attempted = scored + dropped_no_verdict`, and
each key summed over the two lanes equals the global `counts[key]`.

**`core_brain/kpi.py` — `funnel["lanes"]`:** `{"live": {"counts": {...},
"filters": [...]}, "regular": {...}}`, `filters` built with the same mapper as
the global list, or `None` when the snapshot has no `lanes` (an older artifact)
— never zeros. `funnel["filters"]` (global) is unchanged.

**`dashboard/static/app.js`:** `rowLane(m) -> "live" | "regular" | "unknown"`;
`unknown` rows appear only under All. Lane views read
`funnel.lanes[lane].counts` / `.filters`; All reads `funnel.counts` /
`funnel.filters`.

## Improvement proposal (Step 5) — one, evidence-based, adopted

Evidence (issue, verbatim): *"Lane-scoped counts in the snapshot (per lane:
candidates, scored, per-gate rejected, eligible) so the dashboard can number each
lane from the same source it renders."*

Proposal (**simplification, adopt-by-default**): publish the per-lane numbers as
a whole per-lane funnel (`counts` + `rejections`) instead of adding a
`n_by_lane` counter to every rejection entry and re-grouping causes in the
dashboard. The dashboard becomes one scope switch; badge and cards for a gate
read the same entry list, so a non-zero badge over an empty list stops being
expressible rather than being tested for. Rejected alternative reason recorded
above. **No scope expansion proposed** — nothing in the issue needs widening.

## Dependency graph

```
T1 (fetch + declarations + market_lane + merge stamping)
 ├── T2 (lane on every exit + snapshot lanes + kpi passthrough)
 │     └── T3 (dashboard reads lane + lane numbers)
 └── T4 (doc: endpoint/pagination/declarations/lane + the feed gap note)
        (needs T1's stat names and T2's block shape; run last)
```

## Tasks

### T1 — [Backend/Logic] Paginated live fetch, declarations, one lane decision [x]
**Size M. Depends on: —. Verify: `python -m pytest -q tests/test_live_event_discovery.py`**

Target files: `scripts/filter_markets.py:1005-1205`, `scoring/selector.py`.

1. Split the fetch out of `live_event_markets` (keep its `(rows, stats)`
   signature — 20+ existing call sites are tests):
   - `_fetch_live_events(session, limit)` → `(events, stats)`: page through
     `LIVE_EVENTS` with `limit=LIVE_EVENT_PAGE (100)` + `offset`, advancing by
     `len(page)` and stopping on an empty or short page, capped at
     `LIVE_EVENT_MAX_PAGES (20)` — the contract `scripts/live_events_probe.py:128-156`
     already documents. On cap: `stats["live_truncated"]=True`. On a later-page
     exception: keep the pages already read, set `stats["live_error"]`.
     Keep the dict-wrapper unwrap and the "non-list payload is an error" rule.
     `stats["live_pages"]` = pages read.
   - `_live_rows(events, stats)` = today's body from `1059-1098`.
   - `_live_declarations(events)` → `{condition_id: {_sport, _event_period,
     _event_score, _event_live, sports_market_type}}` for every market with a
     `conditionId` inside a live, not-ended event, BEFORE the moneyline /
     two-token / live-bar checks. Evidence assembled by the same parent-event
     rules `_live_event_row` uses.
   - `live_event_markets(session, limit)` = fetch + `_live_rows` (behaviour and
     stats otherwise unchanged).
2. `market_lane(row)` in `scoring/selector.py` per the interface contract, with
   a short docstring stating the "never a gate input" rule.
3. `merge_live_event_markets(universe, session, limit)`:
   - fetch once, get rows + declared map;
   - stamp `lane` on **every** merged row from this cycle's map (ignore any
     carried-in lane — old stamps must not survive);
   - a declared row with no admitted live copy gets the declaration evidence
     (`_venue_live` is not introduced; `_live_event`/`live_event`/
     `volatility_exempt` are **not** touched) and counts once in
     `stats["live_rows_declared_only"]`;
   - keep in-place replacement (universe length unchanged) and the existing
     dedup.

### T2 — [Backend/Logic] Lane on every exit; per-lane snapshot; funnel passthrough [x]
**Size L. Depends on: T1. Verify: `python -m pytest -q tests/test_live_event_discovery.py tests/test_live_funnel.py tests/test_redesigned_filter_pipeline.py tests/test_pipeline_snapshot_gates.py`**

Target files: `scripts/filter_markets.py:1264-1708`, `2434-2640`,
`core_brain/kpi.py:705-833`.

1. `evaluate`: compute `lane = market_lane(m)` once, before the first exit, and
   put `"lane": lane` on: `_reject_row` (covers expired/UMA/queue-path/non-binary/
   movement/ratio/velocity/book fetch+parse+malformed+one-sided/decided-mid), the
   trial-identity refusal dict (`1370`), the identity refusal dict (`1393`), the
   book-refusal dict (`1610`), and the eligible row (`1650`). No wrapper
   indirection — six literal fields, one comment on `_reject_row`.
2. `_live_evidence`: also emit `event_live` (`bool(m["_event_live"])`) and
   `sports_market_type`; keep its `{}` for scanned rows. Merge it into the
   book-refusal dict so that exit stops being evidence-free. Never set
   `volatility_exempt` there.
3. `_write_pipeline_snapshot`:
   - `raw.spread`: sample per lane (24 each) and add `lane` to each row.
   - `rejections`: unchanged global entries, plus `lane` on each example.
   - `_row()` (final/picked): add `lane`.
   - new `lanes` block per the interface contract, computed from the full
     populations (`spread_cands` for candidates, `out` for scored/rejected/
     eligible, `picked` for picked) — not from samples.
   - `spreadN`'s dead sibling: none here.
4. `core_brain/kpi.py`: extract the existing filters mapping (`734-755`) into a
   module-level helper, use it for the global list and for each lane, pass
   `lanes` through (`None` when absent), and add to each graduated row `lane`
   (via `market_lane`), `event_live`, `event_period`, `event_score`,
   `volatility_exempt`. `market_feed.py` untouched.

### T3 — [Design/UI] Dashboard: read the lane, number the lane
**Size L. Depends on: T2. Verify: `python -m pytest -q tests/test_kanban_lanes.py tests/test_dashboard_server.py` (plus a `node` run of the new harness scenario)**

Target files: `dashboard/static/app.js:9581-9658`, `9941-9970`, `9972-10229`;
`tests/js/input_lag_harness.cjs`.

1. Replace `isLiveMarket` with `rowLane(m)` (`"live"|"regular"|"unknown"`). Use
   it for every lane filter and for card styling; delete the now-pointless
   per-example lane filtering.
2. Scope the numbers: `const scope = lane === 'all' ? funnel : (funnel.lanes &&
   funnel.lanes[lane]) || null`. Gate grouping, `counts`, the ingestion badge and
   "to evaluate", and the "of N scored" denominator read `scope`. Use `!= null`
   checks, never `||`, so an explicit zero stays zero.
3. `!scope` (older snapshot, lane view): one honest board message — "Lane numbers
   are not available for this snapshot." — instead of zeros or global numbers.
   Pills keep counting graduations by `rowLane`; All keeps the global funnel.
4. Passed bucket: badge and footer count the lane's rendered graduated rows (the
   same list), so a badge can never exceed its cards.
5. `updateLaneBanner` counts come from `rowLane`; add `funnel?.lanes` to
   `boardFingerprint`; reset pills + banner on the "No Market Filter data yet"
   path.
6. `index.html`: no change expected (labels already exist) — touch only if a
   lane label becomes false.
7. New `lane-numbers` scenario in `tests/js/input_lag_harness.cjs`: mount the
   rail, `setRailPage('data-markets')`, `await app.pollStatus()` with a fixture
   shaped like the 2026-10-21 artifacts (137 scored globally, 5 live rows, one
   WTA-style live row), then return the board HTML, the three pill texts and the
   banner HTML for the `live`, `regular` and `all` lanes. Python test in
   `tests/test_kanban_lanes.py` asserts: WTA row in live and not in regular; live
   ingestion/badge/denominator/passed agree; no non-zero badge over an empty
   list; explicit zero stays zero; a snapshot without `lanes` shows the note.
   Replace `test_is_live_market_classification` with a `marketLane`/`rowLane`
   truth table.

### T4 — [Docs] Endpoint doc + operator path
**Size S. Depends on: T1, T2. Verify: read the doc section against the shipped code paths.**

Target file: `docs/polymarket-live-events-endpoint.md`.

- "The endpoint" / "How the ranker consumes it": the fetch is paginated
  (100/page, offset by rows returned, page cap, truncation flag); declarations
  are recorded separately from admission; every emitted row carries `lane`.
- "What this endpoint does NOT do": record the execution-feed gap — priority is
  rank order only (`sort_eligible`); `core_brain/market_feed.py`'s
  `GraduatedMarket` carries no live/lane field, so nothing downstream knows a
  quote is live (locked decision 3).

## Checkpoints

- **CP1 (after T1):** a two-page fake venue yields every live row, a
  kicked-off row in the universe comes out `lane: "live"` at unchanged length,
  and a declared-only row is live without gaining `_live_event`.
- **CP2 (after T2):** one live + one regular row through the five exits all carry
  the right lane; `pipeline.json`'s two lanes add up to `counts`.
- **CP3 (after T3):** rendered board shows the WTA row only in Live, and no lane
  renders a non-zero badge over an empty list.
- **CP4 (after T4):** doc matches the shipped stats keys.

## Risks

1. **The rendered-lane harness is the one unproven step.** `input_lag_harness.cjs`
   has a closed scenario list and stubbed DOM; if `renderScreener` cannot be
   driven far enough to expose a lane's board, the fallback is the same scenario
   asserting on the three pill texts + banner HTML only (still proves the
   classification and the "not available" note), plus a python assertion on the
   data contract. Decide within the first 15 minutes of T3.
2. `raw.spread` grows from ≤24 to ≤48 rows — telemetry only, one reader.
3. Mutating the caller's universe dicts in `merge` matches what
   `row.setdefault("fetch_truncated", ...)` already does; keep the behaviour.

## NOTICED-BUT-NOT-TOUCHING

- `core_brain/market_feed.py:99-105,282-288` — `GraduatedMarket` carries no
  live/lane field, so execution cannot tell a live quote from a regular one.
  Locked decision 3: rank order is the shipped meaning; this is the recorded gap,
  not work in this issue.
- `merge_live_event_markets` keeps the **last** duplicate live copy for a
  replaced row and the **first** for an appended one (same `condition_id`, same
  event) — harmless, left alone.
- `dashboard/static/app.js` `spreadN` local was computed and never used; it is
  removed with the line that computed it (T3), not as a separate cleanup.
- The stale `server/fleet_dash.py` reference at `filter_markets.py:2489` — no
  such module; comment left for a docs-only change later.

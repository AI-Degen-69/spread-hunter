Branch: i744/route-in-play-sports-into-live-lane | Issue: #744

# Todo — #744 live lane from the venue declaration

## T1 — paginated live fetch, declarations, one lane decision
- [x] `_fetch_live_events` (page 100, offset by rows returned, short page stops, cap + `live_truncated`, keep pages on error + `live_error`, `live_pages`)
- [x] `_live_rows` extracted; `live_event_markets` keeps its `(rows, stats)` signature
- [x] `_live_declarations(events)` keyed by `condition_id`, built before moneyline/token/bar checks
- [x] `market_lane(row)` in `scoring/selector.py` (lane field → live flags → regular)
- [x] `merge_live_event_markets` stamps `lane` on every row, adds declared-only evidence, counts `live_rows_declared_only`
- [x] tests: two-page + short-page session, page cap, later-page failure, kickoff re-derivation at unchanged length, declared-only has no `_live_event`, `market_lane` truth table

## T2 — lane on every exit, per-lane snapshot, funnel passthrough
- [x] `lane` on all five `evaluate` exits
- [x] `_live_evidence` emits `event_live` + `sports_market_type`; book-refusal merged
- [x] `raw.spread` sampled per lane with `lane`; examples carry `lane`; `_row()` carries `lane`
- [x] `lanes` block (counts + rejections) from full populations
- [x] `kpi._funnel_from_pipeline`: shared filters mapper, `lanes` passthrough (`None` when absent), graduated `lane` + display fields
- [x] tests: five-exit lane assertions, lane reconciliation, per-lane rejection examples, WTA `markets.json` row → live, `lanes` passthrough/None

## T3 — dashboard reads the lane
- [x] `rowLane` replaces `isLiveMarket`; delete per-example lane filtering
- [x] `scope` switch for counts + filters; ingestion count from lane candidates
- [x] "Lane numbers are not available for this snapshot." for old artifacts
- [x] passed badge/footer from the lane's rendered rows; banner + pills by lane
- [x] `funnel?.lanes` in the fingerprint; reset pills/banner on the no-data path
- [x] `lane-numbers` harness scenario + assertions in `tests/test_kanban_lanes.py`; replace the `isLiveMarket` node test

## T4 — docs
- [x] pagination + declarations-separate-from-admission + per-row `lane` in `docs/polymarket-live-events-endpoint.md`
- [x] record the execution-feed gap (rank order only)

## Done
- [x] targeted checks green, recorded in the PR body
- [x] How to verify block written for the operator (hands-on only)

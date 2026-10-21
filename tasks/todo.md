Branch: i744/route-in-play-sports-into-live-lane | Issue: #744

# Todo — #744 live lane from the venue declaration

## T1 — paginated live fetch, declarations, one lane decision
- [ ] `_fetch_live_events` (page 100, offset by rows returned, short page stops, cap + `live_truncated`, keep pages on error + `live_error`, `live_pages`)
- [ ] `_live_rows` extracted; `live_event_markets` keeps its `(rows, stats)` signature
- [ ] `_live_declarations(events)` keyed by `condition_id`, built before moneyline/token/bar checks
- [ ] `market_lane(row)` in `scoring/selector.py` (lane field → live flags → regular)
- [ ] `merge_live_event_markets` stamps `lane` on every row, adds declared-only evidence, counts `live_rows_declared_only`
- [ ] tests: two-page + short-page session, page cap, later-page failure, kickoff re-derivation at unchanged length, declared-only has no `_live_event`, `market_lane` truth table

## T2 — lane on every exit, per-lane snapshot, funnel passthrough
- [ ] `lane` on all five `evaluate` exits
- [ ] `_live_evidence` emits `event_live` + `sports_market_type`; book-refusal merged
- [ ] `raw.spread` sampled per lane with `lane`; examples carry `lane`; `_row()` carries `lane`
- [ ] `lanes` block (counts + rejections) from full populations
- [ ] `kpi._funnel_from_pipeline`: shared filters mapper, `lanes` passthrough (`None` when absent), graduated `lane` + display fields
- [ ] tests: five-exit lane assertions, lane reconciliation, per-lane rejection examples, WTA `markets.json` row → live, `lanes` passthrough/None

## T3 — dashboard reads the lane
- [ ] `rowLane` replaces `isLiveMarket`; delete per-example lane filtering
- [ ] `scope` switch for counts + filters; ingestion count from lane candidates
- [ ] "Lane numbers are not available for this snapshot." for old artifacts
- [ ] passed badge/footer from the lane's rendered rows; banner + pills by lane
- [ ] `funnel?.lanes` in the fingerprint; reset pills/banner on the no-data path
- [ ] `lane-numbers` harness scenario + assertions in `tests/test_kanban_lanes.py`; replace the `isLiveMarket` node test

## T4 — docs
- [ ] pagination + declarations-separate-from-admission + per-row `lane` in `docs/polymarket-live-events-endpoint.md`
- [ ] record the execution-feed gap (rank order only)

## Done
- [ ] targeted checks green, recorded in the PR body
- [ ] How to verify block written for the operator (hands-on only)

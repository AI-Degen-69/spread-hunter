Branch: i744/route-in-play-sports-into-live-lane | Issue: #744

# Constraints — #744 in-play markets in the live lane, honest lane numbers

One lane decision per row, derived from the venue's live declaration, re-derived
every filter cycle, carried on every row the ranker emits, with lane-scoped
numbers in the snapshot so each lane's count agrees with that lane's cards.
The live fetch paginates so a busy slate is not silently truncated.

## The bar

- **The lane is not a gate.** `market_lane`, the new `lane` field and the
  declaration evidence feed display and accounting only. No change to
  `filter_std_*` thresholds, `MIN_VOLUME_24H_LIVE` ($35,000),
  `MIN_VOLUME_24H` ($50,000), depth/spread/horizon bars,
  `bot_pos_price_max_pair_cost_usd`, the volatility-exemption predicate
  (`filter_markets.py:1481-1482`), `rank_score`, `sort_eligible`, quoting,
  sizing or execution. A diff that changes any of those fails the bar.
- **A declared-only row must not gain admission.** It carries `lane: "live"`
  and display evidence and nothing else: no `_live_event`, no `live_event`, no
  `volatility_exempt` change. Otherwise the lane becomes a back door into the
  live volume floor.
- **Every emitted row carries `lane`**, and its value is `"live"` or
  `"regular"` — never missing, never unknown, from the ranker. Five exits plus
  the `final`/`picked` projections, asserted by test.
- **Lane membership is re-derived every cycle.** No carried-in `lane` survives
  `merge_live_event_markets`. The universe length is unchanged by a kickoff
  (in-place replacement) and a `condition_id` appears once (not scored twice).
- **Lane numbers reconcile, or they are absent.** For each lane:
  `scored = rejected + eligible`, `attempted = scored + dropped_no_verdict`,
  and each key summed over `live` + `regular` equals the global `counts[key]`.
  An older snapshot with no `lanes` block yields `None` through
  `_funnel_from_pipeline` and an explicit "not available" in the dashboard —
  never zeros, never global numbers shown as a lane's.
- **The live fetch is complete or it says so.** Pages of 100, offset advanced by
  the rows actually returned, stop on an empty/short page, cap at
  `LIVE_EVENT_MAX_PAGES`. A cap hit sets `live_truncated`; a later-page failure
  keeps the pages already read and sets `live_error`. A one-request session must
  not satisfy the test.
- **Explicit zero stays zero.** Dashboard lane numbers are read with
  `!= null` checks, never `||` fallbacks.

## Anti-cheat

- No skipped, deleted or weakened tests; no `xfail` on the new cases; no
  assertions removed from `test_redesigned_filter_pipeline.py` (its exact-keys
  assertion at `:560` and `len(examples) <= 4` are **unchanged** by this design).
- No new dependency, no new environment variable, no new background service or
  scheduler.
- No dashboard-side venue fetch.
- Do not rewrite or migrate existing `runtime/*.json` artifacts.

## Targeted checks (run by the agent, not the operator)

```
python -m pytest -q tests/test_live_event_discovery.py tests/test_live_funnel.py
python -m pytest -q tests/test_kanban_lanes.py tests/test_dashboard_server.py
python -m pytest -q tests/test_redesigned_filter_pipeline.py tests/test_pipeline_snapshot_gates.py tests/test_filter_markets_out_dir.py
```

The full `python -m pytest -q` suite is the GitHub CI merge gate on Ubuntu and
Windows; it is not repeated locally during the review/PR stations.

## Deliberate, not a regression

- `raw.spread` in `pipeline.json` may hold up to 48 rows (24 per lane) instead
  of 24. It is a preview sample, read by exactly one consumer (`core_brain/kpi.py`).
- `rejections` (global) is unchanged; `lanes.*.rejections` duplicates up to four
  examples per cause per lane. That duplication is the point: each lane's cards
  are drawn from its own rejections.
- Rows with no lane field (an older `markets.json`) classify from their live
  flags inside `market_lane`; they are not rewritten on disk.

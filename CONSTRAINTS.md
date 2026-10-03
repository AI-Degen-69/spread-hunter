# Constraints: Issue #347 — Poll endpoints inside the page's 5s budget

Branch: i347/dashboard-status-pills-read-gray-poll-endpoints | Issue: #347

## Quality & Tests
- **Zero regressions**: `tests/test_dashboard_snapshot_cache.py`,
  `tests/test_per_run_shadow_heartbeat.py`, `tests/test_ring_tail_read.py`,
  `tests/test_shadow_run_stopwatch.py`, `tests/test_scan_state_shadow.py`,
  `tests/test_scan_cadence_measured.py`, `tests/test_market_meta.py`,
  `tests/test_dashboard_server.py` stay green. Full-repo suite stays with GitHub CI.
- **Every changed behaviour needs a test that fails without the change.**
- **Anti-cheat**: no skipped tests, no deleted assertions, no new suppressions.
- **No new external dependencies**: standard library only (`functools`, `pathlib`,
  `json`, `time`). No new config keys, no new env vars.

## Behaviour Boundaries
- **Server gets faster; the client budget does not move.** `safeJsonFetch`'s 5000 ms
  default in `dashboard/static/app.js` is OUT OF SCOPE and must not change. The fix
  is to answer inside 5 s, not to wait longer.
- **No pill/label/copy/vocabulary change.** `DESIGN.md`'s live-state table,
  `dashboard/static/styles.css` pill classes, and every renderer in `app.js` are
  untouched. The frontend half is issue #348.
- **Caching correctness over cache hits.** A cached `read_shadow_run` still reports
  the run that is *actually* running. Caching is keyed on the active db path and
  TTL-bounded exactly like the sibling `_recent_shadow_runs`; a heartbeat file that
  changes inside the TTL is picked up on the next expiry, which is the same
  staleness the run switcher already accepts.
- **Feed cache invalidation is mtime-keyed, never TTL-only.** `markets.json` and
  `market_universe.json` are rewritten by the ranker. The cache key includes the
  file's `st_mtime_ns`, so a rewritten feed is re-read immediately. A stale
  category on the Active Markets table is a correctness bug, not a perf tradeoff.
- **Read-only endpoints only.** Nothing here mutates a store, a registry, a
  heartbeat, or the ring. `data/orders.db` is never written or deleted.
- **Venue-facing behaviour untouched**: no change to quoting, sizing, selection,
  or any `core_brain.order_manager` path.

## Performance Budgets (measured on the reported store, 1278 heartbeat files)
- `GET /api/system/status` — was 3223 ms, must land well under 1000 ms warm.
- `GET /api/scan-state` — was 12797 ms, must land under 5000 ms.
- `GET /api/kpi` — was >25000 ms, must land under 5000 ms.

## Out of Scope (record, do not fix)
- Heartbeat-file retention/pruning (1278 files is a separate accumulation problem).
- The ring rotation policy for run-scoped rings.
- `safeJsonFetch`'s 5 s default, and all #348 frontend behaviour.
# Constraints - #639 (one log tab per process in dashboard event stream)

Branch: i639/one-log-tab-per-process-in-dashboard | Issue: #639

Replaces #632-scoped file wholesale (Step 0A.0: stale issue scope).

## Zero regressions
- Targeted suites:
  `python -m pytest -q tests/test_dashboard_server.py -k "cycle_stream or event_stream or event_ticker"`
  `python -m pytest -q tests/test_positions_live_marks.py`
  Full suite = CI gate.
- Existing `/api/cycle-stream` route, `_cycle_stream_sse()`, and `mark` frames (snapshot, delta, reset) MUST remain 100% backward-compatible and unchanged.
- Existing live-marks panel functionality MUST NOT be regressed.

## Deliberate contracts (not regressions)
- `/api/cycle-stream` remains dedicated to mark frames and legacy cycle stream readers.
- `/api/log-stream` is a read-only SSE endpoint dedicated to per-process log and event streaming.
- Process spawning in `dashboard/server.py` remains untouched (`stdout=subprocess.DEVNULL`). No child stdout interception.
- Venue behavior remains untouched: zero order placement, zero venue network calls.

## Anti-cheat
- No skipped or disabled tests, no weakened assertions.
- Buffer isolation invariant: evictions in the Shadow Rehearsal buffer (capped at 200) MUST NOT evict entries in the Live Run or Filter Loop buffers.
- Plain text log rows must be strictly HTML-escaped (`esc()`) before DOM insertion.

# Plan — Station II: #639 One log tab per process in dashboard event stream

Branch: i639/one-log-tab-per-process-in-dashboard | Issue: #639
Title: [ADD] One log tab per process in the dashboard event stream
Size: Standard (4 files, backend SSE route + frontend tabs + tests) | Type: Code + Design/UI
Labels: enhancement, ready-for-agent | Base: main == origin/main, 0/0, tree clean.

## Context & Problem
The dashboard event ticker currently consumes a single shared 200-line buffer (`app.js:1257`) fed by `/api/cycle-stream`. A chatty rehearsal ring (e.g. `runtime/shadow-*.jsonl`, up to 28 MB) evicts filter loop (`runtime/rerank.log`, `scripts/filter_loop.py`) and live trading engine events (`runtime/cycle_events.jsonl`). Terminal logs (`rerank.log`) are currently invisible in the UI.

## Scope & Target Deliverables
1. Backend (`dashboard/server.py`):
   - Add `_resolve_log_families(run_id=None)` returning per-family source metadata (ring path, text logs, availability reason).
   - Add `_log_stream_sse()` generator following multiple sources with independent tail replay, rotation handling, and keepalive frames.
   - Register route `GET /api/log-stream` supporting repeated `family` query params (`live`, `filter`, `shadow`) and optional sanitized `run_id`.
   - Preserve `/api/cycle-stream` and its `mark` frames completely unchanged.
2. Markup & Styling (`dashboard/static/index.html` & `styles.css`):
   - In `#event-ticker-card`, add `role="tablist"` tab strip with buttons `.ticker-tab-btn` for `all`, `filter`, `shadow`, and `live`.
   - Add hidden `<select>` rehearsal run picker for the shadow tab.
   - Dynamic active file source subtitle.
   - Styling for tabs, active states, and connection status.
3. Frontend State & Stream (`dashboard/static/app.js`):
   - Decouple `allTickerEvents` into per-family ring buffers (capped at 200 items each): `tickerBuffers.all`, `filter`, `shadow`, `live`.
   - Implement `connectLogStream()` connecting to `/api/log-stream`.
   - Keep `connectSSE()` on `/api/cycle-stream` for live `mark` frames.
   - Tab switching re-renders from the target buffer without reconnecting SSE.
   - In-tab filtering (ALL / TRADES / MARKET FILTER / ALERTS) applies within the active tab.
4. Test Harnesses & Pytest (`tests/`):
   - Update `tests/js/event_stream_harness.cjs` to handle log stream format.
   - Create `tests/js/event_stream_tabs_harness.cjs` verifying tab isolation, arrival sorting, and HTML escaping.
   - Add backend tests in `tests/test_dashboard_server.py` verifying replay isolation, rotation handling, parameter validation, and harness execution.

## CodeRabbit Intake (adopted / rejected / unverified)
- ADOPTED:
  - Separate `GET /api/log-stream` endpoint; leaves `/api/cycle-stream` completely untouched for live marks.
  - Event contract: `source` (handshake), `log` (`{family, source, event|text}`), `rotate` (`{family, source}`).
  - Buffer isolation: 4 independent buffers (200 rows each) preventing cross-stream eviction.
  - Plain text log tailing (`runtime/rerank.log`) with raw-path escaping.
  - Run picker for shadow rehearsals when multiple runs exist.
- REJECTED:
  - Capturing subprocess stdout/stderr (dashboard subprocesses spawn with `DEVNULL`; out of scope).
  - Creating new database tables or altering ring formats.
- [RESOLVED FROM CODE]:
  - `event_stream_harness.cjs` uses mock `EventSource`; will support both direct message passing and named `log` event listener.
  - Inode rotation on Windows falls back to `st_ctime_ns` via existing `_ring_file_key()`.

## Improvement Proposal (adopted)
- Evidence: `index.html:249-251` shows static subtitle "Real-time SSE feed from live/runtime/cycle_events.jsonl".
- Proposal: Update subtitle dynamically on tab switch to indicate active file sources (e.g. `runtime/rerank.log` on Filter, `runtime/shadow-<id>.jsonl` on Shadow, `runtime/cycle_events.jsonl` on Live).

## Task Breakdown

### Task 1: Backend Log Family Resolver & SSE Generator [Backend/Logic] [x]
- Files: `dashboard/server.py`
- Description:
  - Implement `_resolve_log_families(run_id=None)` to map `live`, `filter`, `shadow` to their respective ring and text log files.
  - Implement `_log_stream_sse()` to follow multiple files concurrently with independent offsets, replay caps (`SSE_REPLAY_LINES`), and rotation detection.
  - Register `GET /api/log-stream` with parameter validation (sanitized `run_id`, valid family names).
- Depends on: None
- Size: M
- Verification: `python -m pytest -q tests/test_dashboard_server.py -k "log_stream"`

### Task 2: Dashboard Ticker Tab Strip Markup & Styles [Design/UI] [x]
- Files: `dashboard/static/index.html`, `dashboard/static/styles.css`
- Description:
  - Add tab strip (`.ticker-tab-btn`) with `role="tab"` for All, Filter Loop, Shadow Rehearsal, and Live Run.
  - Add shadow run `<select>` picker (hidden until needed).
  - Add dynamic source indicator element.
  - Add CSS classes in `styles.css` for tab styling, active states, and connection badges.
- Depends on: None
- Size: S
- Verification: Markup structure inspection and browser inspection.

### Task 3: Client Per-Family Buffer & LogStream Integration [Frontend/Logic]
- Files: `dashboard/static/app.js`
- Description:
  - Replace single `allTickerEvents` array with per-family isolated buffers (`tickerBuffers`).
  - Implement `connectLogStream()` consuming `/api/log-stream` `source`, `log`, and `rotate` events.
  - Retain `connectSSE()` on `/api/cycle-stream` for live `mark` frames.
  - Wire tab click handlers to `setTickerTab()`, switching active feed and updating empty states.
  - Wire shadow run picker to re-subscribe `connectLogStream(selectedRunId)`.
- Depends on: Task 1, Task 2
- Size: M
- Verification: `python -m pytest -q tests/test_dashboard_server.py -k "cycle_stream or event_stream"`

### Task 4: Test Harnesses & Regression Suite [Verification/Tests]
- Files: `tests/js/event_stream_harness.cjs`, `tests/js/event_stream_tabs_harness.cjs`, `tests/test_dashboard_server.py`
- Description:
  - Update `event_stream_harness.cjs` for compatibility.
  - Write `tests/js/event_stream_tabs_harness.cjs` verifying tab isolation, arrival order, category filtering within tabs, and `<script>` escaping.
  - Add `test_event_stream_tabs_harness` in `tests/test_dashboard_server.py`.
  - Add backend tests for `/api/log-stream` rotation, parameter validation, and multi-stream replay.
- Depends on: Task 1, Task 3
- Size: M
- Verification: `python -m pytest -q tests/test_dashboard_server.py -k "cycle_stream or event_stream or event_ticker"`

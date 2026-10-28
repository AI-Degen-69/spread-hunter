# Implementation Plan — Issue #855: Human-readable structured event stream with bracketed tags, colors, and process filtering

Branch: `plan_github_issue_855` | Issue: #855

## Summary

Replace raw process log streams and awkward fallback sentences in the dashboard event ticker with a clean, colorized, human-readable notification feed. Features include:
1. Monospace bracketed alignment tags (`[TIME] [PROCESS] [EVENT] <MESSAGE>`).
2. Server-side chronological replay aggregation across all active log families with timestamp inference for legacy log lines.
3. Plain-English translations and sensitive token redaction (wallet `0x...`, secrets, filesystem paths).
4. Multi-dimensional filtering by process family, component, and event category (`CONNECTIONS`, `TRADES`, `ALERTS`, `MARKET FILTER`).

Right-sizing: **Standard** (touches 4 dashboard core files + tests, internal architectural refinement, no database changes, no external dependencies).
Task type: **Code** + **Design/UI**.
Quick-fix lane: **No** (`quick-fix` label absent, Standard tier).

## Code-review intake (CodeRabbit plan comment on #855)

One reading; HTML echo ignored.
- **Adopted:**
  - Collect replay from all requested families on the server, sort chronologically by timestamp, and yield after all `source` hello frames.
  - Inferred timestamp fallback for console lines lacking full ISO date (relative to file mtime / nearest known timestamp).
  - Additive `ts`, `level`, and `message` fields on text log frames.
  - Four-part row model (`time`, `processTag`, `eventTag`, `message`, `severity`).
  - Fixed-width `ch` grid layout for tag columns with responsive narrow-screen wrapping.
  - Component dropdown beside family tabs and new `CONNECTIONS` category filter.
  - Expanding `ALERTS` to include warn/bad severity rows and console errors.
- **Rejected:**
  - Over-splitting into 9 granular subtasks; consolidated into 3 atomic vertical slices.
- **[UNVERIFIED] items resolved by live code spot-check:**
  - `_parse_event_ts` location verified at `dashboard/server.py:3484`. A tolerant ring timestamp parser will be placed adjacent to it.
  - `_log_stream_sse` generator verified at `dashboard/server.py:4200`. Current implementation yields family-by-family during replay; will be replaced by in-memory collector and chronological sort.
  - `buildStreamSentence` verified at `dashboard/static/app.js:1262`. Generic fallback at line 1344 produces `" on a market"` when `market_slug` is absent; will be sanitized.
  - `event_stream_harness.cjs` and `event_stream_tabs_harness.cjs` verified in `tests/js/`. Note: harnesses require passing `dashboard/static/app.js` as `process.argv[2]`.

## Improvement Proposal (Evidence-based, Adopted by Default)

- **Evidence:**
  `dashboard/server.py:4238-4331` replays log lines family-by-family without cross-source chronological alignment, causing old filter logs to interleave awkwardly after live events.
  `dashboard/static/app.js:1344` falls back to `${prefix} on ${market}: ${humanizeAction(action)}${because}.` where `market` defaults to `'a market'` and `because` echoes raw payload parameters, exposing wallet addresses (`auth_ok wallet: 0x...`) in plain text.
- **Committed Enhancement:**
  1. **Replay Aggregator & Stabilizer:** SSE replay buffers entries across all families, infers missing timestamps from file mtime and adjacent lines, and performs a stable sort on `(timestamp, family_order, source_order, line_seq)` before streaming.
  2. **Wallet Redaction & Sentence Polish:** Enforce an allow-list on `extra` parameters for main row messages, redacting wallet addresses and connection URLs to `[SHOW DETAILS]`, and omit `" on a market"` when no market slug exists.

## Interface Contracts & Schemas

### 1. SSE Text Log Frame Enrichment (`/api/log-stream`)
- **Existing schema preserved:**
  `{"family": "filter", "source": "text", "file": "rerank.log", "text": "..."}`
- **Additive enrichment:**
  `{"family": "filter", "source": "text", "file": "rerank.log", "text": "...", "ts": "2026-10-28T02:00:00Z", "level": "INFO", "message": "Scan pass 1 completed"}`

### 2. Browser Row Model (`app.js`)
- `time`: Local formatted `HH:MM:SS` (e.g. `16:28:00`)
- `processTag`: Uppercase bracketed tag, max 8 chars (e.g. `FILTER`, `USER_WS`, `MKT_WS`, `TRADER`, `GUARD`, `QUERY`)
- `eventTag`: Uppercase bracketed tag, max 8 chars (e.g. `AUTH_OK`, `CONNECTED`, `TRADE`, `ORDER`, `BOOK`, `FILL`, `SCAN`, `LOG`, `WARN`, `ERR`)
- `message`: Plain-English concise string (e.g. `User stream connected`, `Bought UP 3 shares at $0.48 on Brazil election.`)
- `severity`: CSS class `good` (green), `warn` (amber), `bad` (red), `info` (cyan/blue), `muted` (gray)
- `rawDetail`: Collapsible raw text line preserved for `[SHOW DETAILS]`

### 3. CSS Grid Layout (`styles.css`)
```css
.ticker-row {
  display: grid;
  grid-template-columns: 10ch 11ch 11ch 1fr;
  gap: 8px;
  align-items: baseline;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
}
@media (max-width: 768px) {
  .ticker-row {
    grid-template-columns: 10ch 11ch 1fr;
  }
  .ticker-col-msg {
    grid-column: 1 / -1;
    margin-top: 2px;
  }
}
```

## Task Decomposition

### Dependency Graph
```
Task 1 (Server chronological replay & text metadata)
   │
   ▼
Task 2 (Structured row model, bracketed tags, colors, and layout)
   │
   ▼
Task 3 (Process/component filtering, category tabs, and harnesses)
```

---

### Task 1: Server Replay Chronological Aggregation & Text Frame Metadata [x]
- **Size:** M
- **Domain:** `[Backend/Logic]`
- **Assigned Helper:** `test-driven-development`
- **Depends on:** None
- **Target Files:**
  - `dashboard/server.py`
  - `tests/test_dashboard_server.py`
- **What gets built:**
  1. Add `_parse_replay_ts(ts)` accepting ISO-8601 with optional microseconds/offsets, returning Unix float or None.
  2. Add `_parse_console_line(line, file_mtime, last_known_ts)` parsing Filter loop headers, `HH:MM:SS` shadow lines, Python tracebacks, and extracting `(ts_iso, level, clean_message)`.
  3. In `_log_stream_sse`:
     - Emit all `event: source` hellos first for all selected families.
     - Collect initial replay events across all families into a single buffer.
     - Calculate timestamps for all collected records (inheriting previous/next in file if missing, falling back to mtime).
     - Stably sort buffer on `(ts_unix, family_idx, source_idx, line_idx)`.
     - Yield sorted replay events.
     - Enrich text frames with additive `ts`, `level`, and `message` (both during replay and follow loop).
  4. Unit tests in `tests/test_dashboard_server.py` verifying cross-family chronological ordering, source-first hello delivery, and text frame enrichment.
- **Verification:**
  `python -m pytest -q tests/test_dashboard_server.py -k "log_stream_sse"`

---

### Task 2: Structured Row Model, Bracketed Tags, Colors, and Plain-English Sentences
- **Size:** M
- **Domain:** `[Design/UI]` + `[Frontend/Logic]`
- **Assigned Helper:** `frontend-ui-engineering`
- **Depends on:** Task 1
- **Target Files:**
  - `dashboard/static/app.js`
  - `dashboard/static/styles.css`
- **What gets built:**
  1. Process and event tag formatters: `streamProcessTag(ev, family)`, `streamEventTag(ev)`.
  2. Severity classifier: `streamSeverityClass(ev)` returning `good`, `warn`, `bad`, `info`, `muted`.
  3. Enhanced `buildStreamSentence`:
     - Plain-English messages for `user_ws_*` lifecycle events (`user_ws_connected`, `user_ws_auth_ok`, `user_ws_reconnecting`, `user_ws_disconnected`, `user_ws_trade`, `user_ws_order`).
     - Background runner and lifecycle messages (`guardian_reactive_wake`, `rerank_done`, `rerank_error`, `single_leg_transition`, `user_fill_received`).
     - Grammar corrections (singular/plural shares, omit empty merge collateral string).
     - Redact wallet addresses and connection secrets from main message.
     - Sanitize fallback: omit `" on a market"` when market slug is absent.
  4. Console line translator in `connectLogStream` for text frames: converts Filter headers, worker heartbeats, and ranker status into clean notifications.
  5. Update `appendTickerEvent` to construct the unified row model and store `rawDetail` for the details drawer.
  6. In `renderTickerFeed`: render structured spans (`.ticker-col.ticker-time`, `.ticker-col.ticker-process`, `.ticker-col.ticker-event`, `.ticker-col.ticker-msg`) wrapped in square brackets.
  7. CSS styles in `styles.css`: CSS Grid columns with fixed `ch` widths, severity color variables (`--green-ok`, `--amber-warn`, `--red-alert`, `--open`, `--text-muted`), and responsive wrapping.
- **Verification:**
  `node tests/js/event_stream_harness.cjs dashboard/static/app.js`

---

### Task 3: Dual Process/Component & Event Category Filtering, and Regression Suite
- **Size:** M
- **Domain:** `[Frontend/Logic]` + `[Testing]`
- **Assigned Helper:** `test-driven-development`
- **Depends on:** Task 2
- **Target Files:**
  - `dashboard/static/index.html`
  - `dashboard/static/app.js`
  - `tests/js/event_stream_harness.cjs`
  - `tests/js/event_stream_tabs_harness.cjs`
  - `tests/test_dashboard_server.py`
- **What gets built:**
  1. In `index.html`:
     - Add `<select id="ticker-component-select" class="ticker-component-select" aria-label="Filter by component">` next to process tabs.
     - Add `<button class="ticker-filter-btn" data-filter="connections" type="button">CONNECTIONS</button>` to filter buttons.
  2. In `app.js`:
     - Dynamically populate Component dropdown choices from active events in the selected tab (e.g. `ALL`, `USER_WS`, `MKT_WS`, `TRADER`, `GUARD`, `QUERY`, `FILTER`).
     - Multi-tier filtering in `renderTickerFeed`: Tab (Family) -> Component -> Category.
     - Category matching:
       - `CONNECTIONS`: matches websocket and daemon connection/lifecycle actions.
       - `ALERTS`: matches guardrail alerts, stop-loss, plus any row with `warn` or `bad` severity and console errors.
       - `TRADES`: fills, exits, completed pairs, merges, redeems.
       - `MARKET FILTER`: screener/filter scans.
     - Contextual empty state text mentioning active tab, component, and filter.
  3. Update Node test harnesses:
     - `tests/js/event_stream_harness.cjs`: test new tag format, wallet redaction, user ws translations, and severity classes.
     - `tests/js/event_stream_tabs_harness.cjs`: test component dropdown selection, combined filters, and console line translation.
  4. Update Python integration tests in `tests/test_dashboard_server.py` to ensure full test suite compatibility.
- **Verification:**
  `node tests/js/event_stream_tabs_harness.cjs dashboard/static/app.js`
  `node tests/js/event_stream_harness.cjs dashboard/static/app.js`
  `python -m pytest -q tests/test_dashboard_server.py tests/test_cycle_stream.py`

---

## Checkpoints

- **Checkpoint 1 (after Task 1):** Server tests pass with chronological replay aggregation across families and enriched text frames.
- **Checkpoint 2 (after Task 2):** Event stream harness passes with aligned bracketed tags, severity colors, and plain-English sentences without wallet leakage.
- **Checkpoint 3 (after Task 3):** Full integration passes with component dropdown, CONNECTIONS filter, and green local regression suite.

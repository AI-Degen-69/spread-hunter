# Todo — Issue #855: Human-Readable Structured Event Stream

Branch: `plan_github_issue_855` | Issue: #855

- [x] **Task 1: Server Replay Chronological Aggregation & Text Frame Metadata**
  - [x] Implement `_parse_replay_ts` and `_parse_console_line` in `dashboard/server.py`
  - [x] Refactor `_log_stream_sse` to emit all `source` hellos first, buffer historical replay, and sort chronologically by timestamp
  - [x] Add additive `ts`, `level`, and `message` to text log frames during replay and live follow
  - [x] Add unit tests in `tests/test_dashboard_server.py` for chronological replay across families and text metadata
  - [x] Verify Task 1: `python -m pytest -q tests/test_dashboard_server.py -k "log_stream_sse"`

- [ ] **Task 2: Structured Row Model, Bracketed Tags, Colors, and Plain-English Sentences**
  - [ ] Implement `streamProcessTag`, `streamEventTag`, and `streamSeverityClass` in `dashboard/static/app.js`
  - [ ] Add plain-English translations for user websocket, background lifecycle, and ranker console lines in `buildStreamSentence` and `connectLogStream`
  - [ ] Redact sensitive tokens (wallet addresses, secrets, urls) and sanitize fallback sentences (remove awkward `"on a market"`)
  - [ ] Update `appendTickerEvent` and `renderTickerFeed` to produce aligned column spans with square-bracketed padding tags
  - [ ] Implement CSS Grid rules and severity color styles (`good`, `warn`, `bad`, `info`, `muted`) in `dashboard/static/styles.css`
  - [ ] Verify Task 2: `node tests/js/event_stream_harness.cjs dashboard/static/app.js`

- [ ] **Task 3: Dual Process/Component & Event Category Filtering, and Regression Suite**
  - [ ] Add Component dropdown `<select id="ticker-component-select">` and `CONNECTIONS` filter button to `dashboard/static/index.html`
  - [ ] Implement multi-tier filtering (Tab -> Component -> Category) and dynamic component list population in `dashboard/static/app.js`
  - [ ] Expand `ALERTS` category to include warn/bad severity rows and console errors
  - [ ] Update `tests/js/event_stream_harness.cjs` and `tests/js/event_stream_tabs_harness.cjs` to test new row structure, component filtering, and translations
  - [ ] Update assertions in `tests/test_dashboard_server.py`
  - [ ] Verify Task 3: `node tests/js/event_stream_tabs_harness.cjs dashboard/static/app.js` and `python -m pytest -q tests/test_dashboard_server.py tests/test_cycle_stream.py`

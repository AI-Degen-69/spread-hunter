# CONSTRAINTS.md — Issue #855: Human-Readable Structured Event Stream

Branch: `plan_github_issue_855` | Issue: #855

## 1. Safety & Production Integrity

- **LIVE is the default.** Never run opening commands (`quote`, `complete`, Trader loop start, dashboard START button) during development or verification.
- **`data/orders.db` is strictly read-only.** Never rewrite, modify, or delete `data/orders.db`.
- **Dynamic Risk Caps:** Do not touch `core_brain/config.py` risk limits or execution parameters.
- **On-disk Log Schema Preserved:** Do not alter the NDJSON record structure in `cycle_events.jsonl` or other disk log files. Any SSE payload enrichment (`ts`, `level`, `message`) on text frames is purely additive at the API layer.

## 2. Writable Seams (Exclusively These)

- `dashboard/server.py`: Timestamp parsing helper (`_parse_replay_ts`), console line parser (`_parse_console_line`), replay aggregation and chronological sorting in `_log_stream_sse`, and enrichment of text frames.
- `dashboard/static/app.js`: Process/component tag mapping, event tag mapping, severity classification, stream sentence translation improvements, redaction of sensitive tokens, row model in `appendTickerEvent`, and multi-tier filtering logic in `renderTickerFeed`.
- `dashboard/static/index.html`: Component filter dropdown `<select id="ticker-component-select">` and category button `CONNECTIONS`.
- `dashboard/static/styles.css`: CSS Grid rules for aligned ticker columns, fixed `ch` widths, responsive wrap rules, and severity classes (`good`, `warn`, `bad`, `info`, `muted`).
- `tests/test_dashboard_server.py`: Server-side SSE chronological replay tests and payload contract tests.
- `tests/js/event_stream_harness.cjs`: Unit assertions for plain-English translations, tag formatting, and security redaction.
- `tests/js/event_stream_tabs_harness.cjs`: Multi-process tab and component filter assertions.

## 3. Frozen Seams (Do Not Modify)

- `core_brain/cycle_stream.py`
- `core_brain/order_manager.py`
- `core_brain/config.py`
- `core_brain/trader_loop.py`
- `core_brain/shadow_run.py`
- `server.ts`

## 4. Anti-Cheat & Quality Gates

- **Zero skips / no deletions:** No existing tests in `tests/test_dashboard_server.py` or `tests/test_cycle_stream.py` may be disabled, deleted, or weakened.
- **No external dependencies:** No new Python packages in `requirements.txt` / `pyproject.toml`, and no new npm packages.
- **Node.js Harness Compatibility:** Headless verification via `node tests/js/event_stream_harness.cjs dashboard/static/app.js` and `node tests/js/event_stream_tabs_harness.cjs dashboard/static/app.js` must execute cleanly without browser DOM dependencies.
- **Merge Gate Target:** Targeted checks must pass locally:
  `python -m pytest -q tests/test_dashboard_server.py tests/test_cycle_stream.py`
  `node tests/js/event_stream_harness.cjs dashboard/static/app.js`
  `node tests/js/event_stream_tabs_harness.cjs dashboard/static/app.js`

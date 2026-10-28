# SPEC.md — Issue #855: Human-Readable Structured Event Stream with Bracketed Tags, Colors, and Process Filtering

Branch: `plan_github_issue_855` | Issue: #855

## 1. Goal

Transform the dashboard's "Live Execution & Cycle Event Stream" (`/api/log-stream` and `#event-ticker`) into an aligned, color-coded, human-readable notification feed with bracketed structural tags (`[TIME] [PROCESS] [EVENT]`), unified chronological server-side replay across all log families, and fine-grained filtering by originating process/component and event category.

## 2. Core Principles & Non-Negotiables

1. **Human-readable by default, raw on demand:** No raw console output (`[rerank.log] Scan iteration 1...`) or awkward generic fallbacks (`Status update on a market: user ws auth ok (auth_ok wallet: 0x...)`) in the primary row. The main row displays clear, plain English. The raw unformatted string is accessible exclusively via the collapsible `[SHOW DETAILS]` drawer.
2. **Security & Privacy (Redaction):** Main event stream rows must never display wallet addresses (`0x...`), API keys, URLs, or filesystem paths.
3. **Structured Visual Alignment:** Columns align vertically using monospace square-bracketed padding tags (`[TIME] [PROCESS] [EVENT] <MESSAGE>`). Tag column widths are fixed in monospace character units (`ch`), preventing jitter across rows.
4. **Color Hierarchy:** Colors reflect status and severity rather than arbitrary service identity:
   - `good` (green / `--green-ok`): Successful connections, auth ok, fills recorded, clean rank passes.
   - `warn` (amber / `--amber-warn`): Reconnecting, retry, timeouts, pause/stop notes.
   - `bad` (red / `--red-alert`): Disconnections, errors, stream exceptions, rank failures.
   - `info` (cyan/blue / `--open`): Trades, quotes, order submissions, market snapshots/ticks.
   - `muted` (gray / `--text-muted`): Normal background scans, timestamps.
5. **Unified Chronological Aggregation:** Historical replay from `/api/log-stream` must aggregate records from all active log families (Live Ring, Filter text logs, Shadow Ring/logs) into a single chronological stream sorted by event timestamp (`ts`), breaking ties stably.
6. **No Scope Creep:** Do NOT modify `core_brain/config.py`, trading algorithms, or on-disk NDJSON log schemas (`cycle_events.jsonl`).

## 3. Architecture & Data Flow

```
┌────────────────────────────────────────────────────────┐
│                   Server (server.py)                   │
│                                                        │
│  Live Ring (cycle_events) ──┐                          │
│  Filter Logs (rerank.log)  ──┼──> Collect & Parse Ts   │
│  Shadow Ring (shadow.db)   ──┘       │                 │
│                                      ▼                 │
│                              Chronological Sort        │
│                                      │                 │
│                         Emit SSE Replay Frames         │
│                         (additive ts, level, msg)      │
└──────────────────────────────┬─────────────────────────┘
                               │ SSE /api/log-stream
                               ▼
┌────────────────────────────────────────────────────────┐
│                   Browser (app.js)                     │
│                                                        │
│  Receive Event ──> Normalize Row Model                 │
│                    (time, processTag, eventTag, msg)   │
│                           │                            │
│  Filter by Tab (Family) ──┼──> Filter by Component     │
│                           │           │                │
│                           ▼           ▼                │
│                    Filter by Category (TRADES, ALERTS) │
│                           │                            │
│                           ▼                            │
│                    Render CSS Grid (Fixed ch widths)   │
│                    [TIME] [PROCESS] [EVENT] [MESSAGE]  │
└────────────────────────────────────────────────────────┘
```

## 4. Acceptance Criteria

- [ ] Replay across families emits all `source` hellos first, followed by historical events strictly sorted in chronological timestamp order.
- [ ] Text log frames contain additive fields `ts`, `level`, and `message` without breaking existing consumers or changing `cycle_events.jsonl` on disk.
- [ ] Events render with visually aligned, bracketed tags: `[HH:MM:SS] [PROCESS] [EVENT]` in fixed-width columns.
- [ ] Row severity applies consistent CSS classes (`good`, `warn`, `bad`, `info`, `muted`).
- [ ] Plain-English messages for User WS, background processes, fills, quotes, and market streams; no wallet addresses or raw errors in the main row.
- [ ] UI provides dual filtering: Process Tabs + Component Dropdown + Event Category Buttons (`ALL`, `TRADES`, `CONNECTIONS`, `MARKET FILTER`, `ALERTS`).
- [ ] Unit & harness tests pass: `python -m pytest -q tests/test_dashboard_server.py tests/test_cycle_stream.py` and Node.js event stream harnesses.

## 5. Explicit Out of Scope

- Modifying core trading logic, order sizing, or risk parameters.
- Changing NDJSON schema on disk or touching `data/orders.db`.
- Introducing third-party frontend frameworks or npm packages.

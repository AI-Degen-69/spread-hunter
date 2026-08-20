# Live-Decision-Visibility Epic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live engine's per-cycle decision flow observable in real time on the `live/dash` dashboard (issues #51, #52/#53, #54, #55).

**Architecture:** The telemetry ring (#51) is already committed on this branch: `live/engine/cycle_stream.py` appends NDJSON to `live/run/cycle_events.jsonl` and logs `cycle_intent` rows to `live.db`. The remaining work is **dashboard-only, additive, read-only**: a Server-Sent-Events tail of the ring (`/api/cycle-stream`) feeding a new "Bot Brains" panel (#52/#53), plus a `/api/scan-state` endpoint that derives SCANNING / IDLE / STALLED and per-cycle skip/pass rationale from the ring + heartbeat + `cycle_intent` (#54). No changes to quote/decision logic, no changes to `/api/state` or `/api/kpi` behaviour.

**Tech Stack:** Python 3.11+, FastAPI + Starlette `StreamingResponse`, SQLite (read-only URI), vanilla HTML/CSS/JS embedded in `live/dash/live_dash.py`, `pytest` (FastAPI `TestClient`).

## Global Constraints

- All work lands in `live/dash/live_dash.py` and `live/tests/test_live_dash.py` only. **Do not modify `live/engine/` or `scripts/`** — importing `engine.cycle_stream.read_ring` is fine; changing those files is not (it would trip the research-log pre-commit hook and pollute the #51 deliverable).
- The dashboard is a single FastAPI file; the page is the `PAGE_HTML` string constant. New UI is additive HTML/CSS/JS inside that constant.
- New routes must be read-only. No `/api/system/*` control surface, no CSRF token needed.
- Everything must be testable hermetically: use `set_db_override` / new `set_ring_override` / `set_heartbeat_override` so tests never touch `live/run/`.
- Live suite command: `cd live && pytest -q`. Simulation suite: `pytest -q` from repo root (must still pass — no root files change, so it will).
- Commit once per issue; the pre-commit hook must pass (no research/ changes needed because we touch only `live/dash/` + `live/tests/`).
- Issue mapping: #52 and #53 are duplicates; implement under **#53**, close #52 as duplicate in Task C. #55 is the epic tracker, closed in Task C.

---

### Task A: SSE cycle-stream endpoint + Bot Brains panel (#52/#53)

**Files:**
- Modify: `live/dash/live_dash.py`
  - imports (~line 28-31): `from typing import Any` → add `Generator, Optional`; `from fastapi.responses import HTMLResponse, JSONResponse` → add `StreamingResponse`.
  - after `STALE_THRESHOLD_SEC = 30.0` (~line 52): add ring constants + overrides.
  - after `/api/kpi` route (~line 1194): add `_cycle_stream_sse` + `/api/cycle-stream` route.
  - `PAGE_HTML`: add CSS block, Bot Brains HTML section, and JS functions + startup call.
- Test: `live/tests/test_live_dash.py` (add imports + 3 tests)

**Interfaces:**
- Consumes: `engine.cycle_stream` ring file schema `{ts, service, cycle, phase, action, market_slug, reason, latency_ms, pid, extra}` (from #51, already on branch).
- Produces:
  - `set_ring_override(path: Path | str | None) -> None`
  - `resolve_ring_path() -> Path`
  - `_cycle_stream_sse(ring_path: Path, tail: int, poll_sec: float) -> Generator[str, None, None]`
  - `GET /api/cycle-stream` → `StreamingResponse` (`text/event-stream`)
  - HTML ids: `bb-status-dot`, `bb-scan-state`, `bb-scan-sub`, `bb-active-pills`, `bb-evaluated`, `bb-passed`, `bb-funnel-fill`, `bb-funnel-skips`, `bb-sparkline`, `bb-decision-log`.

- [ ] **Step 1: Write the failing tests** (append to `live/tests/test_live_dash.py`)

Add imports at the top of the test file (next to the existing `from dash.live_dash import (...)`):

```python
import datetime
import json
```

Extend the existing import to pull in the new names:

```python
from dash.live_dash import (
    PAGE_HTML,
    app,
    compute_scan_state,
    query_db_state,
    resolve_db_path,
    set_db_override,
    set_heartbeat_override,
    set_ring_override,
    _cycle_stream_sse,
)
```

(Note: `compute_scan_state` and `set_heartbeat_override` are introduced in Task B. To keep Task A green in isolation, define those two names as stubs in Task A first — see Step 3 — so the import resolves, then flesh them out in Task B.)

Append the three Task A tests:

```python
def test_cycle_stream_route_registered():
    """GET /api/cycle-stream is served as an SSE endpoint."""
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/cycle-stream" in paths


def test_cycle_stream_sse_replays_tail_and_follows_appends(tmp_path):
    """The SSE generator replays the ring tail, then follows new appends."""
    ring = tmp_path / "cycle_events.jsonl"
    ring.write_text(
        json.dumps({"service": "engine", "phase": "scanning", "action": "tick"}) + "\n",
        encoding="utf-8",
    )
    gen = _cycle_stream_sse(ring, tail=50, poll_sec=0.01)
    first = next(gen)
    assert "data:" in first
    assert '"action": "tick"' in first

    with ring.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"service": "fleet", "phase": "quoting", "action": "decide"}) + "\n"
        )

    deadline = time.time() + 3.0
    saw_follow = False
    while time.time() < deadline:
        if '"action": "decide"' in next(gen):
            saw_follow = True
            break
    gen.close()
    assert saw_follow


def test_page_html_contains_bot_brains_panel():
    """The page ships the Bot Brains panel shell and its SSE hookup."""
    assert "Bot Brains" in PAGE_HTML
    assert 'id="bb-active-pills"' in PAGE_HTML
    assert 'id="bb-decision-log"' in PAGE_HTML
    assert 'id="bb-sparkline"' in PAGE_HTML
    assert "/api/cycle-stream" in PAGE_HTML
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd live && pytest -q tests/test_live_dash.py -k "cycle_stream or bot_brains" -v`
Expected: FAIL — `ImportError` (names not defined), then route/HTML assertions fail once imports are stubbed.

- [ ] **Step 3: Write minimal implementation**

**(a) Imports.** In `live/dash/live_dash.py`, change:

```python
from typing import Any
```
to:
```python
from typing import Any, Generator, Optional
```

and:

```python
from fastapi.responses import HTMLResponse, JSONResponse
```
to:
```python
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
```

**(b) Ring constants + overrides.** Immediately after `STALE_THRESHOLD_SEC = 30.0`:

```python
CYCLE_RING_NAME = "cycle_events.jsonl"
SSE_REPLAY_LINES = 50
SSE_POLL_SEC = 0.5
SSE_KEEPALIVE_SEC = 15.0
SCAN_STALL_THRESHOLD_SEC = 90.0

_ACTIVE_RING_OVERRIDE: Path | None = None
_ACTIVE_HEARTBEAT_OVERRIDE: Path | None = None


def set_ring_override(path: Path | str | None) -> None:
    """Point the cycle-stream/scan-state endpoints at a specific ring file (tests)."""
    global _ACTIVE_RING_OVERRIDE
    _ACTIVE_RING_OVERRIDE = Path(path) if path else None


def set_heartbeat_override(path: Path | str | None) -> None:
    """Point scan-state at a specific heartbeat file (tests)."""
    global _ACTIVE_HEARTBEAT_OVERRIDE
    _ACTIVE_HEARTBEAT_OVERRIDE = Path(path) if path else None


def resolve_ring_path() -> Path:
    if _ACTIVE_RING_OVERRIDE is not None:
        return _ACTIVE_RING_OVERRIDE
    return LIVE_ROOT / "run" / CYCLE_RING_NAME


def resolve_heartbeat_path() -> Path:
    if _ACTIVE_HEARTBEAT_OVERRIDE is not None:
        return _ACTIVE_HEARTBEAT_OVERRIDE
    return LIVE_ROOT / "run" / "live_poll_heartbeat.json"
```

**(c) Task A stubs for Task B names** (keeps the extended test import working before Task B lands):

```python
def compute_scan_state(
    last_event_ts: Optional[float],
    hb_ts: Optional[float],
    now: float,
    active_phases: set[str],
    stall_threshold: float = SCAN_STALL_THRESHOLD_SEC,
) -> tuple[str, Optional[float]]:
    """Task B fills this in; stub returns IDLE so Task A imports resolve."""
    return "IDLE", None
```

**(d) SSE generator + route.** After the `/api/kpi` route (after its `get_kpi` function ends, before `PAGE_HTML`):

```python
def _cycle_stream_sse(
    ring_path: Path,
    tail: int = SSE_REPLAY_LINES,
    poll_sec: float = SSE_POLL_SEC,
) -> Generator[str, None, None]:
    """Yield SSE frames for the cycle-telemetry ring: replay tail, then follow appends.

    The engine rotates the ring past 500 lines by atomically replacing the file.
    When the file shrinks we emit an ``event: rotate`` frame and re-sync from the
    new tail so the client can clear its log before the replay.
    """
    last_keepalive = time.time()

    def _frame(line: str) -> str:
        return f"data: {line.strip()}\n\n"

    offset = 0
    if ring_path.exists():
        try:
            with open(ring_path, "r", encoding="utf-8", errors="replace") as fh:
                tail_lines = fh.readlines()[-tail:]
            offset = ring_path.stat().st_size
            for line in tail_lines:
                if line.strip():
                    yield _frame(line)
        except OSError:
            pass

    while True:
        try:
            if not ring_path.exists():
                time.sleep(poll_sec)
                continue
            size = ring_path.stat().st_size
            if size < offset:
                offset = 0
                yield "event: rotate\ndata: {}\n\n"
            if size > offset:
                with open(ring_path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    for line in fh:
                        if line.strip():
                            yield _frame(line)
                offset = size
            if time.time() - last_keepalive >= SSE_KEEPALIVE_SEC:
                yield ": keepalive\n\n"
                last_keepalive = time.time()
            time.sleep(poll_sec)
        except OSError:
            time.sleep(poll_sec)


@app.get("/api/cycle-stream")
def cycle_stream_events():
    """Server-Sent-Events tail of live/run/cycle_events.jsonl."""
    return StreamingResponse(
        _cycle_stream_sse(resolve_ring_path()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

**(e) CSS.** In `PAGE_HTML`, insert immediately after the `.pill-stale` rule (search for `.pill-stale { background: var(--red-bg)`):

```css
    /* BOT BRAINS (live decision flow via SSE) */
    .bot-brains { border-left: 3px solid rgba(56,189,248,0.4); }
    .bot-brains-grid { display:flex; flex-wrap:wrap; gap:14px; align-items:stretch; }
    .bb-col { background:rgba(15,23,42,0.5); border:1px solid rgba(148,163,184,0.12); border-radius:8px; padding:10px 12px; }
    .bb-status { display:flex; align-items:center; gap:10px; min-width:190px; }
    .bb-status-dot { width:14px; height:14px; border-radius:50%; background:#64748b; flex:0 0 auto; }
    .bb-status-dot.scanning { background:#10b981; animation: bbPulse 1.2s infinite; }
    .bb-status-dot.idle { background:#94a3b8; animation: bbBreathe 3.2s ease-in-out infinite; }
    .bb-status-dot.stalled { background:#ef4444; animation: bbPulse 0.8s infinite; }
    .bb-scan-state { font-family:'JetBrains Mono',monospace; font-size:15px; font-weight:700; }
    .bb-scan-state.scanning { color:#10b981; }
    .bb-scan-state.idle { color:#94a3b8; }
    .bb-scan-state.stalled { color:#ef4444; }
    .bb-scan-sub { font-size:11px; color:var(--text-muted); }
    .bb-pills { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .bb-pill { font-family:'JetBrains Mono',monospace; font-size:11px; padding:4px 8px; border-radius:999px; border:1px solid rgba(148,163,184,0.2); color:var(--text-secondary); white-space:nowrap; }
    .bb-pill.busy { border-color:rgba(56,189,248,0.6); color:#7dd3fc; animation: bbPulse 1.1s infinite; }
    .bb-funnel { flex:1 1 220px; }
    .bb-funnel-label { font-size:11px; color:var(--text-muted); margin-bottom:6px; }
    .bb-funnel-bar { height:10px; background:rgba(30,41,59,0.8); border-radius:6px; overflow:hidden; }
    .bb-funnel-fill { height:100%; width:0%; background:linear-gradient(90deg,#0ea5e9,#10b981); transition:width 0.6s ease; }
    .bb-funnel-skips { font-size:10px; color:#fbbf24; margin-top:6px; min-height:14px; }
    .bb-spark { flex:0 1 220px; }
    .bb-spark-label { font-size:11px; color:var(--text-muted); margin-bottom:4px; }
    .bb-log { margin-top:12px; font-family:'JetBrains Mono',monospace; font-size:11px; line-height:1.5; max-height:180px; overflow-y:auto; background:rgba(2,6,23,0.6); border:1px solid rgba(148,163,184,0.1); border-radius:8px; padding:8px 10px; }
    .bb-log-line { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .bb-log-ts { color:#64748b; }
    .bb-log-ms { color:#64748b; }
    .bb-log-line .ph-scanning { color:#38bdf8; }
    .bb-log-line .ph-filtering { color:#a78bfa; }
    .bb-log-line .ph-quoting { color:#10b981; }
    .bb-log-line .ph-settling { color:#f59e0b; }
    .bb-log-line .ph-idle { color:#94a3b8; }
    .bb-log-line .ph-waiting { color:#64748b; }
    .bb-log-line .ph-reconciling { color:#f472b6; }
    .kpi-glow { box-shadow: 0 0 14px rgba(56,189,248,0.55); }
    @keyframes bbPulse { 0%,100% { opacity:1; } 50% { opacity:0.35; } }
    @keyframes bbBreathe { 0%,100% { transform:scale(1); opacity:0.6; } 50% { transform:scale(1.5); opacity:1; } }
```

**(f) HTML section.** In `PAGE_HTML`, insert immediately before the `<!-- LEVEL 1: RUN-LEVEL STRATEGY METRICS ... -->` comment:

```html
    <!-- BOT BRAINS: LIVE DECISION FLOW (SSE from /api/cycle-stream) -->
    <section class="panel bot-brains">
      <div class="section-title" style="color:#38bdf8;">
        <span>Bot Brains &mdash; Live Decision Flow</span>
        <span class="badge" style="background:rgba(56,189,248,0.15);border-color:rgba(56,189,248,0.3);color:#38bdf8;">Live</span>
      </div>
      <div class="bot-brains-grid">
        <div class="bb-col bb-status">
          <div class="bb-status-dot" id="bb-status-dot"></div>
          <div>
            <div class="bb-scan-state" id="bb-scan-state">CONNECTING</div>
            <div class="bb-scan-sub" id="bb-scan-sub">waiting for cycle stream</div>
          </div>
        </div>
        <div class="bb-col bb-pills" id="bb-active-pills">
          <span class="bb-pill" data-service="engine">engine &middot; &mdash;</span>
          <span class="bb-pill" data-service="fleet">fleet &middot; &mdash;</span>
          <span class="bb-pill" data-service="screener">screener &middot; &mdash;</span>
        </div>
        <div class="bb-col bb-funnel">
          <div class="bb-funnel-label">Filter funnel: <span id="bb-evaluated">&mdash;</span> evaluated &rarr; <span id="bb-passed">&mdash;</span> passed</div>
          <div class="bb-funnel-bar"><div class="bb-funnel-fill" id="bb-funnel-fill"></div></div>
          <div class="bb-funnel-skips" id="bb-funnel-skips">skip reasons appear here</div>
        </div>
        <div class="bb-col bb-spark">
          <div class="bb-spark-label">scan latency (ms)</div>
          <canvas id="bb-sparkline" width="220" height="36"></canvas>
        </div>
      </div>
      <div class="bb-log" id="bb-decision-log"><!-- 12-line color-coded tail --></div>
    </section>
```

**(g) JS.** Insert before the `async function pollState()` definition:

```javascript
    // BOT BRAINS: live decision flow via Server-Sent Events.
    const bbSpark = [];
    let bbLastLine = null;
    let bbLogEl, bbDotEl, bbStateEl, bbSubEl, bbPillsEl, bbEvalEl, bbPassEl,
        bbFillEl, bbSkipsEl, bbCanvas;

    function bbSparkPush(v) {
      if (v === null || v === undefined || isNaN(v)) return;
      bbSpark.push(v);
      if (bbSpark.length > 60) bbSpark.shift();
      bbDrawSpark();
    }

    function bbDrawSpark() {
      if (!bbCanvas) return;
      const ctx = bbCanvas.getContext('2d');
      const w = bbCanvas.width, h = bbCanvas.height;
      ctx.clearRect(0, 0, w, h);
      if (!bbSpark.length) return;
      const max = Math.max(...bbSpark, 1);
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      bbSpark.forEach((v, i) => {
        const x = (i / Math.max(1, bbSpark.length - 1)) * (w - 2) + 1;
        const y = h - 2 - (v / max) * (h - 6);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    function bbSetScanState(kind, label, sub) {
      if (!bbDotEl) return;
      bbDotEl.className = 'bb-status-dot ' + kind;
      bbStateEl.className = 'bb-scan-state ' + kind;
      bbStateEl.textContent = label;
      bbSubEl.textContent = sub || '';
    }

    function bbApplyEvent(ev) {
      const line = JSON.stringify(ev);
      if (line === bbLastLine) return;
      bbLastLine = line;

      const service = ev.service || 'engine';
      const phase = ev.phase || 'idle';
      const pill = bbPillsEl.querySelector('.bb-pill[data-service="' + service + '"]');
      if (pill) {
        pill.innerHTML = esc(service) + ' &middot; <b>' + esc(phase) + '</b>';
        pill.classList.toggle('busy', !['idle', 'waiting'].includes(phase));
      }

      const phaseCls = 'ph-' + String(phase).replace(/[^a-z]/gi, '');
      const when = (ev.ts || '').slice(11, 19);
      const who = service.slice(0, 4);
      const market = ev.market_slug || '·';
      const reason = ev.reason ? ' — ' + ev.reason : '';
      const ms = (typeof ev.latency_ms === 'number' && ev.latency_ms > 0)
        ? '(' + ev.latency_ms + 'ms)' : '';
      const div = document.createElement('div');
      div.className = 'bb-log-line';
      div.innerHTML =
        '<span class="bb-log-ts">' + esc(when) + '</span> ' +
        '<span class="' + phaseCls + '">[' + esc(who) + ':' + esc(phase) + ']</span> ' +
        esc(ev.action || '') + ' ' + esc(market) + esc(reason) + ' ' +
        '<span class="bb-log-ms">' + ms + '</span>';
      bbLogEl.appendChild(div);
      while (bbLogEl.children.length > 12) bbLogEl.removeChild(bbLogEl.firstChild);
      bbLogEl.scrollTop = bbLogEl.scrollHeight;

      if (typeof ev.latency_ms === 'number' && ev.latency_ms > 0) bbSparkPush(ev.latency_ms);
      document.querySelectorAll('.kpi-tile').forEach(t => {
        t.classList.add('kpi-glow');
        clearTimeout(t._glowT);
        t._glowT = setTimeout(() => t.classList.remove('kpi-glow'), 700);
      });
    }

    function renderBotBrainsFunnel(kpi) {
      if (!bbEvalEl) return;
      const f = (kpi && kpi.funnel) || {};
      bbEvalEl.textContent = f.raw_count !== undefined ? f.raw_count : '--';
      bbPassEl.textContent = f.final_count !== undefined ? f.final_count : '--';
      const total = f.raw_count || 0;
      const passed = f.final_count || 0;
      bbFillEl.style.width = (total ? Math.round(100 * passed / total) : 0) + '%';
      const skips = (f.filters || []).map(fl => fl.cause + ' (' + fl.n + ')').slice(0, 3).join(' · ');
      bbSkipsEl.textContent = skips || 'no skips recorded';
    }

    function bbOpenStream() {
      bbLogEl = document.getElementById('bb-decision-log');
      bbDotEl = document.getElementById('bb-status-dot');
      bbStateEl = document.getElementById('bb-scan-state');
      bbSubEl = document.getElementById('bb-scan-sub');
      bbPillsEl = document.getElementById('bb-active-pills');
      bbEvalEl = document.getElementById('bb-evaluated');
      bbPassEl = document.getElementById('bb-passed');
      bbFillEl = document.getElementById('bb-funnel-fill');
      bbSkipsEl = document.getElementById('bb-funnel-skips');
      bbCanvas = document.getElementById('bb-sparkline');
      bbSetScanState('idle', 'CONNECTING', 'waiting for cycle stream');
      const es = new EventSource('/api/cycle-stream');
      es.onmessage = (m) => {
        try { bbApplyEvent(JSON.parse(m.data)); } catch (e) {}
      };
      es.addEventListener('rotate', () => {
        if (bbLogEl) bbLogEl.innerHTML = '';
        bbLastLine = null;
      });
      es.onerror = () => bbSetScanState('stalled', 'RECONNECTING', 'cycle stream dropped');
      es.onopen = () => bbSetScanState('idle', 'IDLE', 'stream connected');
    }
```

**(h) Hook into pollState + startup.** Inside `pollState`, right after `renderFunnel(kpi);` add:

```javascript
        renderBotBrainsFunnel(kpi);
```

And near the bottom, before `// Initial poll and recurring loop`, add:

```javascript
    bbOpenStream();
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd live && pytest -q tests/test_live_dash.py -k "cycle_stream or bot_brains" -v`
Expected: PASS.

Also run the full live dashboard test module to catch regressions:
Run: `cd live && pytest -q tests/test_live_dash.py -v`
Expected: PASS (existing tests unaffected — additive only).

- [ ] **Step 5: Commit**

```bash
git add live/dash/live_dash.py live/tests/test_live_dash.py
git commit -m "feat(live/dash): Bot Brains panel + SSE cycle-stream endpoint (#53)"
```

---

### Task B: filter rationale + scan-vs-idle signal (#54)

**Files:**
- Modify: `live/dash/live_dash.py` (flesh out `compute_scan_state`, add helpers + `/api/scan-state` route, wire the status dot to scan state in JS).
- Test: `live/tests/test_live_dash.py` (4 more tests).

**Interfaces:**
- Consumes: `resolve_ring_path()`, `resolve_heartbeat_path()`, `resolve_db_path(_ACTIVE_DB_OVERRIDE)`; ring schema; heartbeat file `live/run/live_poll_heartbeat.json` shape `[{"ts": <ms>, "iso": ..., "pid": ..., "cycle": N, "errors": N}]`; `cycle_intent` table columns `ts, cycle, market_slug, top_skip_reason, top_pass_reason, intent_count, submitted, cancelled, latency_ms, run_id`.
- Produces:
  - `compute_scan_state(last_event_ts, hb_ts, now, active_phases, stall_threshold) -> (str, Optional[float])`
  - `_read_engine_heartbeat() -> dict`
  - `_read_cycle_intent_rows(db_path, limit=200) -> list[dict]`
  - `_parse_event_ts(ts) -> Optional[float]`
  - `_last_per_service(events) -> dict[str, tuple[str, Optional[float]]]`
  - `GET /api/scan-state` → JSON `{scan_state, seconds_since_heartbeat, seconds_since_scan, last_scan_ts, services, decisions_logged, skip_reasons, pass_reasons}`

- [ ] **Step 1: Write the failing tests**

Append to `live/tests/test_live_dash.py`:

```python
def test_compute_scan_state_stalled_when_heartbeat_missing():
    state, age = compute_scan_state(None, None, 1_000_000.0, {"scanning"})
    assert state == "STALLED"
    assert age is None


def test_compute_scan_state_stalled_when_heartbeat_stale():
    now = 1_000_000.0
    state, age = compute_scan_state(now - 5, now - 120, now, {"quoting"})
    assert state == "STALLED"
    assert age == 120.0


def test_compute_scan_state_scanning_vs_idle():
    now = 1_000_000.0
    state, _ = compute_scan_state(now - 5, now - 5, now, {"quoting"})
    assert state == "SCANNING"
    state, _ = compute_scan_state(now - 5, now - 5, now, {"idle", "waiting"})
    assert state == "IDLE"


def test_scan_state_endpoint_reports_rationale_and_stall(client, temp_db, tmp_path):
    """/api/scan-state derives state from ring+heartbeat and skip/pass from cycle_intent."""
    con = sqlite3.connect(str(temp_db))
    con.execute(
        "INSERT INTO cycle_intent (ts, cycle, market_slug, intent_count, "
        "top_skip_reason, top_pass_reason, run_id) VALUES (?,?,?,?,?,?,?)",
        (time.time(), 1, "mkt-a", 0, "price_band", None, "live"),
    )
    con.execute(
        "INSERT INTO cycle_intent (ts, cycle, market_slug, intent_count, "
        "top_skip_reason, top_pass_reason, run_id) VALUES (?,?,?,?,?,?,?)",
        (time.time(), 2, "mkt-b", 2, None, "edge_ok", "live"),
    )
    con.commit()
    con.close()

    ring = tmp_path / "cycle_events.jsonl"
    ring.write_text(
        json.dumps({
            "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "service": "screener", "cycle": 1, "phase": "scanning",
            "action": "rerank_done", "market_slug": "", "reason": "",
            "latency_ms": 1.0,
        }) + "\n",
        encoding="utf-8",
    )
    hb = tmp_path / "heartbeat.json"
    hb.write_text(
        json.dumps([{"ts": int(time.time() * 1000), "cycle": 1, "errors": 0}]),
        encoding="utf-8",
    )

    set_ring_override(ring)
    set_heartbeat_override(hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    assert res.status_code == 200
    data = res.json()
    assert data["scan_state"] in {"SCANNING", "IDLE", "STALLED"}
    assert data["seconds_since_heartbeat"] is not None
    assert {"reason": "price_band", "count": 1} in data["skip_reasons"]
    assert {"reason": "edge_ok", "count": 1} in data["pass_reasons"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd live && pytest -q tests/test_live_dash.py -k "scan_state or compute_scan_state" -v`
Expected: FAIL — `/api/scan-state` route missing (404), `compute_scan_state` stub returns IDLE.

- [ ] **Step 3: Write minimal implementation**

Replace the Task A `compute_scan_state` stub with the real implementation, and add the helpers + route after `_cycle_stream_sse` (before the `/api/cycle-stream` route is fine; keep `/api/scan-state` after `/api/cycle-stream`):

```python
def compute_scan_state(
    last_event_ts: Optional[float],
    hb_ts: Optional[float],
    now: float,
    active_phases: set[str],
    stall_threshold: float = SCAN_STALL_THRESHOLD_SEC,
) -> tuple[str, Optional[float]]:
    """Classify the fleet as SCANNING, IDLE, or STALLED.

    STALLED  -- the engine heartbeat has not advanced within `stall_threshold`
                (or is absent entirely): a real alarm, not an empty table.
    SCANNING -- heartbeat fresh AND some service did active-phase work
                (scanning/filtering/quoting/settling) in the recent window.
    IDLE     -- heartbeat fresh but no active-phase work in the window.
    """
    age = None
    if hb_ts is not None:
        age = max(0.0, now - hb_ts)
    if hb_ts is None or (age is not None and age > stall_threshold):
        return "STALLED", age
    if active_phases & {"scanning", "filtering", "quoting", "settling"}:
        return "SCANNING", age
    return "IDLE", age


def _parse_event_ts(ts: Any) -> Optional[float]:
    """Parse an ISO-8601 ring timestamp to a Unix timestamp, or None."""
    if not ts:
        return None
    try:
        dt = datetime.datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ")
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _last_per_service(events: list[dict]) -> dict[str, tuple[str, Optional[float]]]:
    """Latest (phase, unix ts) per service from ring events."""
    out: dict[str, tuple[str, Optional[float]]] = {}
    for ev in events:
        svc = str(ev.get("service") or "engine")
        ts = _parse_event_ts(ev.get("ts"))
        if svc not in out or (
            ts is not None and (out[svc][1] is None or ts > out[svc][1])
        ):
            out[svc] = (str(ev.get("phase") or ""), ts)
    return out


def _read_engine_heartbeat() -> dict[str, Any]:
    """Read live/run/live_poll_heartbeat.json, returning {} when absent/invalid."""
    try:
        data = json.loads(resolve_heartbeat_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, list) and data and isinstance(data[-1], dict):
        return data[-1]
    return {}


def _read_cycle_intent_rows(db_path: Path | str, limit: int = 200) -> list[dict]:
    """Last `limit` cycle_intent rows in read-only mode; [] when unavailable."""
    path = Path(db_path)
    if not path.exists():
        return []
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT ts, cycle, market_slug, top_skip_reason, top_pass_reason, "
            "intent_count, submitted, cancelled FROM cycle_intent "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


@app.get("/api/scan-state")
def get_scan_state():
    """SCANNING / IDLE / STALLED plus per-cycle skip/pass rationale (read-only)."""
    now = time.time()
    events = []
    try:
        from engine.cycle_stream import read_ring
        events = read_ring(resolve_ring_path(), tail=400)
    except Exception:
        events = []

    hb = _read_engine_heartbeat()
    hb_ts = (hb.get("ts") or 0) / 1000.0 if hb.get("ts") else None

    window = now - 60.0
    active_phases: set[str] = set()
    last_event_ts: Optional[float] = None
    last_scan_ts: Optional[float] = None
    for ev in events:
        ts = _parse_event_ts(ev.get("ts"))
        if ts is None:
            continue
        if last_event_ts is None or ts > last_event_ts:
            last_event_ts = ts
        if ts >= window:
            active_phases.add(str(ev.get("phase") or ""))
        if str(ev.get("service") or "") == "screener" and (
            last_scan_ts is None or ts > last_scan_ts
        ):
            last_scan_ts = ts

    state, hb_age = compute_scan_state(last_event_ts, hb_ts, now, active_phases)

    rows = _read_cycle_intent_rows(resolve_db_path(_ACTIVE_DB_OVERRIDE))
    skip_counts: dict[str, int] = {}
    pass_counts: dict[str, int] = {}
    for r in rows:
        sk = r.get("top_skip_reason")
        pk = r.get("top_pass_reason")
        if sk:
            skip_counts[sk] = skip_counts.get(sk, 0) + 1
        if pk:
            pass_counts[pk] = pass_counts.get(pk, 0) + 1

    return JSONResponse({
        "scan_state": state,
        "seconds_since_heartbeat": round(hb_age, 1) if hb_age is not None else None,
        "seconds_since_scan": (
            round(max(0.0, now - last_scan_ts), 1) if last_scan_ts is not None else None
        ),
        "last_scan_ts": last_scan_ts,
        "services": {
            svc: {"phase": phase, "last_ts": ts}
            for svc, (phase, ts) in _last_per_service(events).items()
        },
        "decisions_logged": len(rows),
        "skip_reasons": sorted(
            [{"reason": k, "count": v} for k, v in skip_counts.items()],
            key=lambda x: -x["count"],
        ),
        "pass_reasons": sorted(
            [{"reason": k, "count": v} for k, v in pass_counts.items()],
            key=lambda x: -x["count"],
        ),
    })
```

**(JS wiring)** — extend `pollState` to also fetch `/api/scan-state` and drive the dot. Replace the `Promise.all` block:

```javascript
        const [stateRes, kpiRes, scanRes] = await Promise.all([
          fetch('/api/state'),
          fetch(`/api/kpi${selectedRunId ? '?run_id=' + encodeURIComponent(selectedRunId) : ''}`),
          fetch('/api/scan-state')
        ]);
```

and after `renderBotBrainsFunnel(kpi);` add:

```javascript
        renderScanState(scanRes);
```

Then add this function next to `renderBotBrainsFunnel`:

```javascript
    function renderScanState(scanRes) {
      if (!scanRes || !scanRes.ok) {
        bbSetScanState('stalled', 'STALLED', 'scan-state unavailable');
        return;
      }
      scanRes.json().then(scan => {
        const kind = scan.scan_state === 'SCANNING' ? 'scanning'
          : scan.scan_state === 'STALLED' ? 'stalled' : 'idle';
        let sub = '';
        if (scan.seconds_since_scan !== null && scan.seconds_since_scan !== undefined) {
          sub = 'last scan ' + Math.round(scan.seconds_since_scan) + 's ago';
        }
        if (scan.scan_state === 'STALLED') {
          const hb = scan.seconds_since_heartbeat;
          sub = 'STALLED — no heartbeat for ' + (hb !== null && hb !== undefined ? Math.round(hb) + 's' : '?');
        }
        bbSetScanState(kind, scan.scan_state, sub);
        if (bbSkipsEl && scan.skip_reasons && scan.skip_reasons.length) {
          bbSkipsEl.textContent = scan.skip_reasons
            .map(s => s.reason + ' (' + s.count + ')').slice(0, 3).join(' · ');
        }
      }).catch(() => {});
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd live && pytest -q tests/test_live_dash.py -k "scan_state or compute_scan_state" -v`
Expected: PASS.

Then the whole live suite:
Run: `cd live && pytest -q`
Expected: PASS (all live tests, including the #51 telemetry suite).

- [ ] **Step 5: Commit**

```bash
git add live/dash/live_dash.py live/tests/test_live_dash.py
git commit -m "feat(live/dash): filter rationale + scan-vs-idle signal (#54)"
```

---

### Task C: close the tracker (#55) + duplicate (#52) + file polish issues

No code. Do this **after** the PR is merged (the tracker shouldn't close while the work is still unmerged).

- [ ] **Step 1: Close #52 as duplicate of #53**

```bash
gh issue close 52 --comment "Duplicate of #53 (identical Layer 2 scope, created by accident). Tracking the Bot Brains panel under #53."
```

- [ ] **Step 2: File the four polish items from #55 as new issues**

```bash
gh issue create --title "Live visibility polish: persist cycle_intent beyond last 200 cycles" --body "Follow-up from #55. Add trend-able retention for cycle_intent (e.g. append-only archive table or periodic rollup) so skip/pass rationale can be charted over runs." --label enhancement
gh issue create --title "Live visibility polish: 'why no orders?' explainer" --body "Follow-up from #55. When fills are empty but the bot is SCANNING, show a one-line explainer on the dashboard (top skip reason for the current cycle)." --label enhancement
gh issue create --title "Live visibility polish: mobile/compact layout for Bot Brains panel" --body "Follow-up from #55. Make the Bot Brains panel usable on narrow viewports." --label enhancement
gh issue create --title "Live visibility polish: replay mode for cycle_events.jsonl" --body "Follow-up from #55. Scrub the cycle_events.jsonl tail to inspect a past decision." --label enhancement
```

- [ ] **Step 3: Close #55 with a summary**

```bash
gh issue close 55 --comment "All three visibility layers landed (#51, #53, #54). Remaining polish split into four new issues."
```

---

## Self-Review

**Spec coverage:** #52/#53 → Task A (SSE endpoint, pills, decision log, funnel bar, sparkline, KPI glow, status dot). #54 → Task B (skip/pass reasons from `cycle_intent`, SCANNING/IDLE/STALLED, evaluated-vs-passed via the existing `kpi.funnel` counts, STALLED wiring, last-scan timestamp + delta). #55 → Task C. #52 duplicate → Task C. #51 already committed on this branch (not re-planned). #27 → separate follow-up plan (see `docs/superpowers/plans/2026-08-20-four-quant-experiments.md`), out of scope for this PR by user decision.

**Placeholder scan:** All code steps carry complete code; no TBD/TODO. The only stub is `compute_scan_state` in Task A, which is explicitly implemented in Task B — that's forward-referencing with a named contract, not a placeholder.

**Type consistency:** `compute_scan_state` signature is identical in Task A (stub) and Task B (real). `_cycle_stream_sse(ring_path, tail, poll_sec)` matches its test call. Override names (`set_ring_override` / `set_heartbeat_override`) match their import and usage. HTML ids referenced in JS (`bb-*`) all exist in the Task A HTML block.

## GSTACK REVIEW REPORT

| Runs | Status | Findings |
|------|--------|----------|
| Self-review (spec coverage, placeholders, types) | PASS | 0 unresolved |
| CEO review pass (failure modes, edge cases, observability) | PASS | 4 fixed, see below |

**VERDICT: CROSS-MODEL absorbed — plan is implementable as written.**

Failure modes checked and designed for:
1. **Infinite SSE stream hangs tests** → generator is a plain `_cycle_stream_sse(...)` unit-tested with `next()`, not a blocking full-HTTP read; route is verified by path registration.
2. **Rotation re-emits the tail** → `event: rotate` frame + client `bbLastLine` dedupe + log clear.
3. **Screener's 600s cadence would false-alarm STALLED** → STALLED keys off the engine heartbeat (`live_poll_heartbeat.json`), which advances every poll, not the screener.
4. **Tests must not touch `live/run/`** → `set_ring_override` / `set_heartbeat_override` / existing `set_db_override` make every disk read hermetic.

NO UNRESOLVED DECISIONS

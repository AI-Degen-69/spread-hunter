# Shadow Tape Fill Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task with review checkpoints. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make shadow tape reads distinguish actual empty evidence from unusable or incomplete input, and show per-market diagnostics in both the run log and dashboard without changing the queue-aware fill rule.

**Architecture:** Add a typed detailed trade-tape reader in `core_brain.markets` while retaining `recent_trades(...) -> dict` for existing callers. Pass its result through shadow settlement, persist per-run/per-market/per-visit diagnostics in the shadow database, and expose a read-only dashboard endpoint and Active Markets column. Live reconciliation and `LiveFillEngine` remain untouched.

**Tech Stack:** Repository-supported Python, `requests`, dataclasses, SQLite, FastAPI, vanilla JavaScript, pytest, Node.js.

## Global Constraints

- A qualifying print must identify the same token and exact tick-normalized order price and represent trade flow capable of reaching a resting BUY.
- Qualifying shares consume the measured queue ahead first.
- Only remaining qualifying shares may credit the simulated order, up to its remaining size, at the order's limit price.
- A price crossing or book update without qualifying trade evidence does not create a fill.
- When no usable tape evidence is available, the fill model credits nothing from that read and explicitly reports unknown evidence rather than a measured zero. For partial or truncated reads, only validated volume from usable rows/walks may be credited; the report must preserve the incomplete status.
- Do not infer or guarantee a live fill from a last-traded-price display, best-bid change, book-level disappearance, or time resting.
- Do not ignore queue priority or use a probabilistic fill fallback when the tape cannot be read.
- Do not change live order placement, live reconciliation, quote selection, or lifecycle policy.
- `core_brain/live_fill_engine.py` and live reconciliation remain unchanged.
- Shadow continues to use its deny-by-default client and explicit non-production DB path.
- Do not open, write, or rewrite `data/orders.db`; all tests use temporary databases.
- Do not run live `quote`, `complete`, Trader, or dashboard **START** commands.
- Operator verification is hands-on and shadow-only; never ask the operator to run pytest.

---

## File Map

| File | Responsibility in this change |
|---|---|
| `core_brain/markets.py` | Parse, validate, paginate, deduplicate, and report public trade-tape reads; preserve the existing dictionary wrapper. |
| `core_brain/shadow_run.py` | Select the detailed reader, identify each per-market visit, and log a concise read/fill summary. |
| `core_brain/shadow_exec.py` | Persist diagnostic records in the explicit shadow store and keep queue credit based only on normalized qualifying volume. |
| `dashboard/server.py` | Expose the latest per-market tape diagnostic from the currently selected DB using read-only SQLite. |
| `dashboard/static/app.js` | Poll the new endpoint and render tape status/evidence in the Active Markets table without presenting stale diagnostics as current. |
| `dashboard/static/index.html` | No structural change expected; the Active Markets table already has a horizontal overflow container. |
| `tests/test_recent_trades_taker_side.py` | Verify detailed reader status, rejection counts, paging, deduplication, and compatibility behavior. |
| `tests/test_shadow_exec.py` | Verify persisted diagnostics, queue-first fills, run isolation, and unavailable-read behavior. |
| `tests/test_shadow_run.py` | Verify per-market visit IDs and log summaries from the actual shadow settlement path. |
| `tests/test_dashboard_server.py` | Verify the shadow-only diagnostic endpoint, read-only access, and its JSON contract. |
| `tests/test_dashboard_shadow_tape.py` | Verify the Active Markets row renders each status and evidence without unsafe HTML interpolation. |

The database table and JSON API are deliberately shadow-specific. They do not add columns to production `orders`, `fills`, or quote tables.

## Interfaces

### Detailed tape reader

Add immutable result types in `core_brain/markets.py`:

```python
@dataclass(frozen=True)
class TapeWalkReport:
    name: str
    status: str
    pages_read: int
    rows_read: int
    qualifying_rows: int
    accepted_volume: float
    rejected_rows: dict[str, int]
    non_qualifying_rows: dict[str, int]
    first_trade_ts: int | None
    last_trade_ts: int | None
    error: str | None = None


@dataclass(frozen=True)
class TradeTapeReport:
    status: str
    volume_by_token_price: dict[str, dict[float, float]]
    walks: tuple[TapeWalkReport, ...]
```

`recent_trades_detailed(condition_id: str, seen: set, limit: int = 500, taker_side: str | None = "SELL") -> TradeTapeReport` becomes the shadow reader. `recent_trades(...) -> dict` remains a compatibility wrapper that returns `report.volume_by_token_price`.

Status contract:

- `complete`: all requested walks finished without malformed rows or failures, and at least one qualifying print was found.
- `empty`: all requested walks finished and returned no qualifying volume.
- `partial`: one walk failed, or malformed rows weakened evidence while another walk or valid rows remain usable. Preserve and pass only validated volume from usable data to the queue model.
- `truncated`: a walk reached the page bound without proving the available tape was exhausted.
- `unavailable`: no requested walk returned usable evidence; pass an empty volume map to the queue model and leave queue state unchanged.

Per-walk status uses `complete`, `empty`, `partial`, `truncated`, or `unavailable` under the same rules. If malformed rows or a failed walk coexist with usable volume, overall status is `partial`; otherwise a page-bound result is `truncated`. Count invalid rows under `rejected_rows`, and count valid but non-qualifying side/duplicate rows separately under `non_qualifying_rows`.

### Shadow persistence and API

The shadow-only table records one row per market visit:

```sql
CREATE TABLE IF NOT EXISTS shadow_tape_reads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    cycle INTEGER NOT NULL,
    condition_id TEXT NOT NULL,
    market_slug TEXT,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    report_json TEXT NOT NULL
);
```

Create `idx_shadow_tape_reads_market_ts` on `(condition_id, ts DESC, id DESC)` so the latest-per-market dashboard read remains efficient as history grows.

Add `write_shadow_tape_read(db_path: Path | str, *, ts: float, cycle: int, condition_id: str, market_slug: str | None, run_id: str, status: str, report: dict) -> None` and `read_shadow_tape_reads(db_path: Path | str, *, limit: int = 500) -> list[dict] | None` in `core_brain.shadow_exec`.

`write_shadow_tape_read` serializes `report` as compact JSON with non-finite numbers refused, then inserts one row. `read_shadow_tape_reads` opens an existing file in SQLite read-only URI mode, returns the latest row per condition across runs ordered newest-first, decodes `report_json` as `report`, returns `None` if the table does not exist, and returns `[]` if the table exists but has no rows. It never creates a database or schema.

The API endpoint is:

```text
GET /api/shadow-tape-health
```

It returns `{ "available": bool, "markets": [...] }`, where each market object includes
`condition_id`, `market_slug`, `run_id`, `cycle`, `ts`, `status`, and `report`. The report
contains the tape walk details and a `settlement` object with `observed_volume`,
`queue_consumed`, `fill_credited`, and per-level data. A live DB or an older shadow DB without the table returns `available: false`;
that state must not be rendered as a successful empty tape.

## Task 1: Detailed, truthful public tape reads

**Files:**
- Modify: `core_brain/markets.py`
- Test: `tests/test_recent_trades_taker_side.py`

**Consumes:** Existing `TRADES_API`, `_walk_tape`, `TRADE_MAX_PAGES`, `TRADE_MAX_OFFSET`, and per-session `seen` set.

**Produces:** `TapeWalkReport`, `TradeTapeReport`, and `recent_trades_detailed(...)`; unchanged `recent_trades(...) -> dict` callers.

- [ ] **Step 1: Add failing detailed-result tests.**

Add `from requests import RequestException` to the test imports and use the existing `paged_tape` fixture. Verify a successful SELL plus maker-side supplement yields `complete` and preserves the normalized volume map; a clean empty response yields `empty`; malformed JSON shape/rows and failed walks are not `empty`; and a full page limit yields `truncated`.

```python
def test_detailed_tape_report_distinguishes_empty_from_unavailable(paged_tape):
    paged_tape({0: [], (0, False): []})

    empty = markets.recent_trades_detailed("0xcond", set())

    assert empty.status == "empty"
    assert empty.volume_by_token_price == {}
    assert [walk.status for walk in empty.walks] == ["empty", "empty"]

    paged_tape({0: RequestException("tape down")})
    unavailable = markets.recent_trades_detailed("0xcond", set())

    assert unavailable.status == "unavailable"
    assert unavailable.volume_by_token_price == {}
    assert unavailable.walks[0].error
```

Add a separate test proving one successful walk's volume survives a failed supplemental walk and the overall status is `partial`. Add assertions that malformed rows increment reasoned counters and never add volume. For a `limit=1` read that returns a full page on every configured page, assert that the overall status is `truncated` while its validated volume map retains those prints.

- [ ] **Step 2: Run the focused test to verify it fails for the missing interface.**

Run in PowerShell:

```powershell
rtk pytest -q tests/test_recent_trades_taker_side.py -k "detailed_tape_report or detailed_tape_partial or detailed_tape_malformed or detailed_tape_truncated"
```

Expected: FAIL because `recent_trades_detailed` and its report types do not exist yet.

- [ ] **Step 3: Implement the detailed walk result and preserve compatibility.**

Refactor `_walk_tape` to return `(TapeWalkReport, staged_identities, normalized_volume)`. Validate row identity, side, token, timestamp, finite positive price/size before admitting a row; count malformed rows by reason in `rejected_rows`, and count valid rows filtered by side or duplicate identity in `non_qualifying_rows`. Track page and row totals and first/last valid trade timestamps. Mark the walk truncated when the configured page bound is reached with a full fresh page. Catch only expected request/JSON/payload failures in the detailed reader; retain usable volume from a successful first walk if the maker supplement fails. Keep a failed walk's identities uncommitted so the next poll can reread them; commit completed walk identities once, including the cross-walk identity bookkeeping needed to avoid double counting.

Use only sanitized failure labels: exception class plus HTTP status when present; never include request URLs, headers, or raw payloads. A non-list JSON response is a malformed walk, not an empty page. Reject `NaN`/infinite prices and sizes with `math.isfinite`.

Aggregate walk statuses according to the status contract above. Keep duplicate rows out of qualifying volume and report them separately. Do not commit identities from a failed walk; commit completed walk identities exactly once. Implement the compatibility wrapper as:

```python
def recent_trades(condition_id, seen, limit=500, taker_side="SELL") -> dict:
    report = recent_trades_detailed(
        condition_id, seen, limit=limit, taker_side=taker_side,
    )
    return report.volume_by_token_price
```

The shadow runner uses the detailed API directly; the wrapper remains for existing readers. Do not coerce a falsey payload such as JSON `null` to `[]` before validating that the payload is a list.

- [ ] **Step 4: Run all tape-reader tests.**

Run:

```powershell
rtk pytest -q tests/test_recent_trades_taker_side.py
```

Expected: all existing dictionary API tests and new status tests pass; existing two-walk de-duplication behavior is unchanged.

- [ ] **Step 5: Commit the independently tested tape-reader change.**

Run:

```powershell
rtk git add core_brain/markets.py tests/test_recent_trades_taker_side.py
rtk git commit -m "feat(shadow): report detailed tape read health" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

Expected: one commit contains only the detailed reader and its tests.

## Task 2: Persist diagnostics and log the evidence beside simulated fills

**Files:**
- Modify: `core_brain/shadow_exec.py`
- Modify: `core_brain/shadow_run.py`
- Test: `tests/test_shadow_exec.py`
- Test: `tests/test_shadow_run.py`

**Consumes:** `TradeTapeReport` and `recent_trades_detailed(...)` from Task 1.

**Produces:** Additive shadow schema, `read_shadow_tape_reads(...)`, and one run-scoped diagnostic per market visit. Fill settlement continues to consume only `volume_by_token_price`.

- [ ] **Step 1: Add failing persistence and settlement tests.**

Add a temporary-DB test that submits a resting order with measured queue 10, returns 15 exact-price qualifying shares in a `TradeTapeReport`, and verifies queue becomes zero, fill size is five at the order limit, and the persisted report row says `complete` with the same qualifying volume. Add tests that unavailable tape leaves queue and fills unchanged while persisting `unavailable`, does not write a misleading zero-valued `queue_marks` row, and that identical market visits from two run IDs stay isolated. Add a partial-read case proving that validated volume from the completed walk is still passed to the queue model, while the persisted and logged overall status stays `partial`.

```python
def test_unavailable_tape_is_not_persisted_as_empty(registry):
    reg, db = registry
    ensure_shadow_tables(db)
    report = TradeTapeReport(
        status="unavailable",
        volume_by_token_price={},
        walks=(TapeWalkReport(
            name="taker", status="unavailable", pages_read=0, rows_read=0,
            qualifying_rows=0, accepted_volume=0.0, rejected_rows={},
            non_qualifying_rows={}, first_trade_ts=None, last_trade_ts=None,
            error="RequestException",
        ),),
    )

    settle_market(
        reg, FakeMarket(), db_path=db, seen=set(),
        traded_fn=lambda cid, seen: report,
        cycle=1,
    )

    saved = read_shadow_tape_reads(db, limit=10)
    assert saved[-1]["status"] == "unavailable"
    assert saved[-1]["report"]["walks"][0]["error"] == "RequestException"
```

- [ ] **Step 2: Run the new shadow test selectors and confirm failure.**

Run:

```powershell
rtk pytest -q tests/test_shadow_exec.py -k "tape_read or tape_report or unavailable_tape"
```

Expected: FAIL because no diagnostic table/read helper or detailed-report settlement input exists.

- [ ] **Step 3: Add additive shadow persistence and report the observed settlement.**

In `ensure_shadow_tables`, create `shadow_tape_reads` and `idx_shadow_tape_reads_market_ts` on `(condition_id, ts DESC, id DESC)`. Add `json` to the module imports. Implement `write_shadow_tape_read(...)` and read-only `read_shadow_tape_reads(...)`; the reader returns `None` for a missing DB/table and uses a SQLite read-only URI for existing DBs. Extend `settle_market` to accept either the existing dictionary callback or `TradeTapeReport`; preserve existing dictionary-only callers without writing fabricated status rows. Add optional `cycle: int = 0`; persist the detailed report for each visit, including reads with no resting orders. Do not decrement saved queue state when a read is unavailable.

For each detailed result, add a `settlement` object to `report_json` with `measurement_status`, nullable `observed_volume`, nullable `total_qualifying_volume`, nullable `unmatched_volume`, nullable `queue_consumed`, nullable `fill_credited`, and one `resting_levels` entry per `(token_id, price)`. Each level includes the exact-price volume once and an `orders` list containing each order's `local_id`, `queue_before`, `queue_consumed`, and `fill_credited`. Derive per-order queue consumption from `queue_before - updated_queue_ahead` returned by `credit_fills`; derive fill credit from the returned fill rows. Use `measurement_status="unavailable"` with null totals for an unavailable read, and `"no_resting_orders"` with null order-level totals when there is no owned resting order. A measured zero is valid only when a usable read and at least one resting level were observed. Log exactly one concise line per detailed visit from `settle_market`, including market slug, run ID, cycle, status, pages/rows, qualifying rows/volume at resting levels, queue consumed, fill credited, and rejection counts. Do not emit raw payloads, secrets, or a success-shaped zero on read failure.

Keep this persistence call contract:

```python
write_shadow_tape_read(
    db_path, ts=now_fn(), cycle=cycle,
    condition_id=market.condition_id, market_slug=_market_slug(market),
    run_id=mine_run_id, status=tape_report.status,
    report={
        **asdict(tape_report),
        "settlement": {
            "measurement_status": settlement["measurement_status"],
            "observed_volume": settlement["observed_volume"],
            "total_qualifying_volume": settlement["total_qualifying_volume"],
            "unmatched_volume": settlement["unmatched_volume"],
            "queue_consumed": settlement["queue_consumed"],
            "fill_credited": settlement["fill_credited"],
            "resting_levels": settlement["resting_levels"],
        },
    },
)
```

Build `settlement` as a dictionary before this call. Implement the writer using the existing connection helper:

```python
payload = json.dumps(report, allow_nan=False, separators=(",", ":"))
with closing(get_connection(Path(db_path))) as conn:
    conn.execute(
        "INSERT INTO shadow_tape_reads "
        "(ts, cycle, condition_id, market_slug, run_id, status, report_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts, cycle, condition_id, market_slug, run_id, status, payload),
    )
    conn.commit()
```

Implement `read_shadow_tape_reads` as a read-only query; the correlated subquery chooses one newest visit per condition across all run IDs:

```python
def read_shadow_tape_reads(db_path, *, limit=500):
    path = Path(db_path)
    if not path.is_file():
        return None
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=2.0)) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'shadow_tape_reads'"
        ).fetchone()
        if exists is None:
            return None
        rows = conn.execute(
            "SELECT t.id, t.ts, t.cycle, t.condition_id, t.market_slug, "
            "t.run_id, t.status, t.report_json FROM shadow_tape_reads AS t "
            "WHERE t.id = (SELECT s.id FROM shadow_tape_reads AS s "
            "WHERE s.condition_id = t.condition_id "
            "ORDER BY s.ts DESC, s.id DESC LIMIT 1) "
            "ORDER BY t.ts DESC, t.id DESC LIMIT ?",
            (max(0, min(int(limit), 500)),),
        ).fetchall()
        return [
            {
                **{key: row[key] for key in row.keys() if key != "report_json"},
                "report": json.loads(row["report_json"]),
            } for row in rows
        ]
```

For an `unavailable` report, normalize the fill map to `{}` and bypass `credit_fills` queue updates. Skip `_record_queue_marks` for that visit because its numeric `traded=0` column cannot represent unavailable evidence. For `partial` or `truncated`, pass only validated volume from completed/usable walks to `credit_fills`; keep the report status unchanged and treat observed volume as a lower bound.

Define settlement totals from the actual `(token_id, price)` intersections between `volume_by_token_price` and the run-owned resting orders. Include `total_qualifying_volume` across all normalized keys and `unmatched_volume = total_qualifying_volume - observed_volume` when at least one resting level was measured, so a complete tape with prints elsewhere is not mistaken for an empty tape. Use null totals when the read is unavailable or there are no owned resting orders. For each unique resting level, count its observed tape volume once; sum each order's queue decrease and returned fill size for the level totals. Do not derive a fill from aggregate tape volume.

In `build_shadow_seam`'s `settling_inventory_fn`, increment a per-market counter before each `settle_market` call, pass it as `cycle`, and use `_default_traded_fn` backed by `recent_trades_detailed`. Keep the existing outer shadow exception handling so persistence failures are visible and do not become fills. Keep `settle_market`'s type backward-compatible for existing dictionary-returning injected callbacks.

- [ ] **Step 4: Run shadow persistence and rehearsal tests.**

Run:

```powershell
rtk pytest -q tests/test_shadow_exec.py tests/test_shadow_run.py
```

Expected: all prior shadow behavior passes; unavailable evidence is persisted distinctly from empty evidence, and no fill is fabricated.

- [ ] **Step 5: Commit the shadow persistence and logging change.**

Run:

```powershell
rtk git add core_brain/shadow_exec.py core_brain/shadow_run.py tests/test_shadow_exec.py tests/test_shadow_run.py
rtk git commit -m "feat(shadow): persist per-cycle tape diagnostics" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

Expected: one commit contains the additive shadow telemetry and its focused tests.

## Task 3: Expose run-scoped tape health in the dashboard

**Files:**
- Modify: `dashboard/server.py`
- Modify: `dashboard/static/app.js`
- Test: `tests/test_dashboard_server.py`
- Create: `tests/test_dashboard_shadow_tape.py`

**Consumes:** `shadow_tape_reads` rows from Task 2.

**Produces:** Read-only `GET /api/shadow-tape-health` and a per-market Tape cell in the Active Markets view.

- [ ] **Step 1: Add failing endpoint and rendering tests.**

With `set_db_override(temp_db)`, initialize the shadow table, insert one diagnostic record for a sample condition, and verify the endpoint returns it. Verify an older DB without the table and a production-mode identity return `available: false`, not an empty-success report; monkeypatch the production identity in this test so no production path is opened. Verify the endpoint does not modify the DB using `PRAGMA data_version` and a read-only open. In `tests/test_dashboard_shadow_tape.py`, invoke the CommonJS export from `dashboard/static/app.js` and prove `EMPTY`, `UNAVAILABLE`, `PARTIAL`, and `TRUNCATED` have distinct visible labels and untrusted tooltip fields are escaped.

```python
def test_shadow_tape_health_is_read_only_and_returns_latest_market_record(
        client, temp_db):
    from core_brain.shadow_exec import ensure_shadow_tables, write_shadow_tape_read

    ensure_shadow_tables(temp_db)
    write_shadow_tape_read(
        temp_db, ts=100.0, cycle=2, condition_id="0xmarket",
        market_slug="sample-market", run_id="shadow-A", status="partial",
        report={
            "settlement": {"observed_volume": 12.0},
            "rejected_rows": {"bad_timestamp": 1},
        },
    )

    response = client.get("/api/shadow-tape-health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["markets"][0]["status"] == "partial"
    assert payload["markets"][0]["report"]["settlement"]["observed_volume"] == 12.0
```

- [ ] **Step 2: Run the new endpoint test to verify it fails.**

Run:

```powershell
rtk pytest -q tests/test_dashboard_server.py -k "shadow_tape_health"
rtk pytest -q tests/test_dashboard_shadow_tape.py
```

Expected: the endpoint test fails because `/api/shadow-tape-health` is not registered, and the Node-driven table test fails because the tape-health renderer is not implemented.

- [ ] **Step 3: Implement a bounded read-only API and dashboard display.**

Add `GET /api/shadow-tape-health` in `dashboard/server.py`. Resolve the configured DB with `resolve_db_path(_ACTIVE_DB_OVERRIDE)` and `resolve_db_identity(...)`; return `available: false, markets: []` for the production registry before opening it. For a shadow DB, use `read_shadow_tape_reads` with its 500-row cap; return `available: false, markets: []` when the table is absent and `available: true, markets: []` when it exists with no observations. Return `available: true` with latest rows otherwise. Propagate read failures through `_read_only_error_response(...)`.

In `app.js`, poll the endpoint with the existing two-second `pollStatus()` batch without counting its failure as backend contact success. Keep `lastShadowTape` and its receipt timestamp. When the endpoint fails, render `STALE` (amber) with the age rather than replaying a prior status as current; when it succeeds with `available: false`, render quiet-gray `NOT AVAILABLE`; only a successful report row may render `EMPTY`, `COMPLETE`, `PARTIAL`, or `TRUNCATED`. Pass the retained payload into `renderOrdersTrades`, `ordersTradesRows`, and `activeMarketsRows`. Add a `Tape` column at the end of `OT_COLUMNS['active-markets']`, update that view's text-sort indexes, and render status, qualifying volume, and a tooltip with reasoned rejected counts. Use `esc(...)` for all API-derived strings. Reuse the existing semantic colors and fonts from `DESIGN.md`; do not add a poll timer.

Use a pure cell helper with this decision order (all returned API text passes through `esc(...)`):

```javascript
function tapeHealthCell(entry, readFailed, ageSec) {
  if (readFailed) return otTag('warn', 'STALE', `${ageSec}s`, 'Tape diagnostics read failed');
  if (!entry) return otTag('quiet', 'NOT AVAILABLE', null, 'No shadow tape diagnostics are available');
  const status = String(entry.status || '').toUpperCase();
  if (!['EMPTY', 'COMPLETE', 'PARTIAL', 'TRUNCATED', 'UNAVAILABLE'].includes(status)) {
    return otTag('quiet', 'NOT AVAILABLE', null, 'Tape status is unknown');
  }
  const tone = status === 'COMPLETE' ? 'good'
    : (status === 'PARTIAL' || status === 'TRUNCATED' || status === 'UNAVAILABLE') ? 'warn' : 'quiet';
  const settlement = entry.report && entry.report.settlement;
  const measured = settlement && settlement.measurement_status === 'measured';
  const value = measured ? settlement.observed_volume : null;
  return otTag(tone, status, value,
    tapeHealthTitle(entry));
}
```

`tapeHealthTitle(entry)` includes the escaped run ID, cycle, observation time, per-walk status/page/row counts, rejection counts, qualifying volume outside resting levels, and queue/fill totals; it explicitly says when no order-level volume was measured. Add the endpoint result lookup by `condition_id` when building `activeMarketsRows`; do not change the sort unit from market rows.

- [ ] **Step 4: Run focused dashboard endpoint and JavaScript behavior tests.**

Run:

```powershell
rtk pytest -q tests/test_dashboard_server.py tests/test_dashboard_shadow_tape.py
```

Expected: the endpoint is read-only and shadow-store scoped, all statuses render distinctly, stale data does not masquerade as current, and the Active Markets table's existing sorting behavior remains intact.

- [ ] **Step 5: Commit the dashboard endpoint and display.**

Run:

```powershell
rtk git add dashboard/server.py dashboard/static/app.js tests/test_dashboard_server.py tests/test_dashboard_shadow_tape.py
rtk git commit -m "feat(dashboard): show shadow tape health" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

Expected: one commit contains the read-only endpoint, Active Markets display, and focused API/DOM tests.

## Task 4: Verify the complete behavior and operator instructions

**Files:**
- Test: `tests/test_recent_trades_taker_side.py`
- Test: `tests/test_shadow_exec.py`
- Test: `tests/test_shadow_run.py`
- Test: `tests/test_dashboard_server.py`
- Test: `tests/test_dashboard_shadow_tape.py`

**Consumes:** Detailed reader, persistence, log, and dashboard interfaces from Tasks 1–3.

**Produces:** Regression proof across the full shadow path and concise operator-facing verification. No `docs/agents/safety.md` change is needed because this adds no new operator command or safety exception.

- [ ] **Step 1: Add one end-to-end shadow regression for a confirmed print through to the UI payload.**

Using a fake tape page with a matching print above the exact-price queue, execute shadow `settle_market` against a temporary DB, then request `/api/shadow-tape-health` against that DB. Assert the simulated fill row and the persisted/API report agree on token, price, queue consumed, credited size, market, run ID, and cycle; assert the concise log line carries the same run/cycle/status and queue/fill totals. Add a second run with an unavailable tape response and assert its report does not say `empty`, creates no fill, and adds no false zero-volume queue mark. Keep both runs in temporary paths and use no venue client.

- [ ] **Step 2: Run the full focused regression set.**

Run:

```powershell
rtk pytest -q tests/test_recent_trades_taker_side.py tests/test_shadow_fills.py tests/test_shadow_exec.py tests/test_shadow_run.py tests/test_dashboard_server.py tests/test_dashboard_shadow_tape.py
```

Expected: all listed suites pass. Do not run the full repository suite locally; GitHub CI is the full-suite merge gate.

- [ ] **Step 3: Commit the end-to-end regression coverage.**

Run:

```powershell
rtk git add tests/test_shadow_exec.py tests/test_shadow_run.py tests/test_dashboard_server.py tests/test_dashboard_shadow_tape.py
rtk git commit -m "test(shadow): cover tape diagnostics end to end" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

Expected: the cross-surface regression is committed without touching the live fill path.

- [ ] **Step 4: Review safety and scope.**

Run:

```powershell
rtk git diff --check
rtk git diff --name-only
```

Confirm `core_brain/live_fill_engine.py`, `data/orders.db`, live order paths, credentials, and quote/lifecycle policy are absent from the change. Confirm no test is skipped or weakened. Review that unavailable/truncated status remains visible through database, API, and dashboard.

- [ ] **Step 5: Complete hands-on shadow verification.**

Use a fresh DB, never `data\orders.db`:

```powershell
python -m core_brain.shadow_run --minutes 5 --db data\shadow_tape_413_01.db --run-id shadow-tape-413
python -m dashboard.server --db data\shadow_tape_413_01.db --port 8799
```

Run the first command in one terminal, then the second in another; use a DB filename that does not already exist and keep the same path in both commands. Dashboard → **Orders & Trades → Active Markets** → inspect **Tape** for the target market. A complete read with no qualifying prints says `EMPTY`; a complete read with prints elsewhere says `COMPLETE` and shows unmatched volume separately from volume at resting prices. An API or payload problem says `UNAVAILABLE`, `PARTIAL`, or `TRUNCATED` with counts/reason. If exact-price qualifying prints exceed the displayed queue, the simulated fill should appear at the resting limit price; if no simulated one-sided fill occurs, this run did not exercise the fill path. Do not press dashboard **START**.

## Dependencies

- Task 1 is independent and produces the detailed tape contract.
- Task 2 depends on Task 1.
- Task 3 depends on Task 2.
- Task 4 depends on Tasks 1–3.

## How to verify

In two PowerShell terminals, choose one unused shadow DB path and use exactly that path for both commands; never use `data\orders.db`:

```powershell
python -m core_brain.shadow_run --minutes 5 --db data\shadow_tape_observability_verify.db --run-id shadow-tape-observability-verify
python -m dashboard.server --db data\shadow_tape_observability_verify.db --port 8799
```

Run the first command in one terminal and the second in another, using an unused DB filename and the same path in both. Open `http://127.0.0.1:8799`, select **Orders & Trades → Active Markets**, and inspect **Tape** for a market with a resting order. A complete read with no qualifying prints says `EMPTY`; a complete read with prints elsewhere says `COMPLETE` and reports unmatched volume separately. A failed/partial/bounded read names `UNAVAILABLE`, `PARTIAL`, or `TRUNCATED` and shows why. Matching print volume first reduces the displayed queue; only volume beyond that queue may appear as a simulated fill at the resting limit. If no qualifying print reaches an active order during the run, the rehearsal did not exercise a fill. Do not press dashboard **START**.

## Completion Criteria

- [ ] A complete empty tape read is visibly different from failed, partial, malformed, and truncated input.
- [ ] Matching exact-price volume drains queue before any shadow order receives a fill.
- [ ] The same persisted per-market/per-cycle report is visible in shadow logs and the dashboard.
- [ ] Unavailable reads leave queue state unchanged and invent no fills; partial/truncated reads credit only validated volume while retaining their incomplete status.
- [ ] Existing `recent_trades(...) -> dict` callers and tests continue to work.
- [ ] Live fill code and production registry remain unchanged.
- [ ] Focused regression suites pass and the hands-on shadow walkthrough is documented.

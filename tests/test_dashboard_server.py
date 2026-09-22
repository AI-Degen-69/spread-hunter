"""Tests for the live execution monitor (live/dash/live_dash.py).

Verifies the single-cycle dashboard behavior across all essential operational states:
1. Empty database (graceful zero state)
2. RESTING pair (open bids, 0 fills)
3. NAKED pair (imbalanced fills, live dollar risk and timer)
4. BALANCED pair (both legs matched, inventory neutral)
5. Stale poll detection (>30s delay)
6. Unattributed order flagging
7. Reconcile lock status
8. Read-only SQLite URI enforcement (no writes possible)
9. FastAPI HTML and JSON endpoint integration
"""
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core_brain.order_registry import SCHEMA
from dashboard.server import (
    PAGE_HTML,
    _PAGE_HTML_FILE,
    app,
    compute_scan_state,
    resolve_db_path,
    set_db_override,
    set_guardrail_heartbeat_override,
    set_heartbeat_override,
    set_ring_override,
    _cycle_stream_sse,
)

NODE = shutil.which("node")

def _read_static(filename):
    """Read a static file for test assertions (replaces PAGE_HTML inline checks)."""
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "dashboard" / "static" / filename
    return p.read_text(encoding="utf-8") if p.exists() else ""




@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary SQLite database initialized with the real registry schema."""
    db_file = tmp_path / "live.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


@pytest.fixture
def client(temp_db):
    """Test client configured to query the temporary test database."""
    set_db_override(temp_db)
    yield TestClient(app)
    set_db_override(None)


def test_read_only_report_errors_share_one_json_shape(client, monkeypatch):
    """Read-only report routes expose the same diagnostic error contract."""
    def fail(*args, **kwargs):
        raise RuntimeError("report backend unavailable")

    monkeypatch.setattr("core_brain.kpi.report", fail)

    for path in (
        "/api/kpi?run_id=read-only-error-test",
        "/api/run-profitability?run_id=read-only-error-test",
        "/api/closed-markets",
    ):
        response = client.get(path)
        assert response.status_code == 500
        assert response.json() == {
            "error": "report backend unavailable",
            "error_type": "RuntimeError",
        }, path


def test_ring_read_failures_are_visible_without_breaking_read_only_shapes(client, tmp_path):
    """Telemetry routes keep 200 empty-state shapes but expose ring failures."""
    unreadable_ring = tmp_path / "ring-directory"
    unreadable_ring.mkdir()
    set_ring_override(unreadable_ring)

    try:
        for path in (
            "/api/scan-state",
            "/api/guardrail-health",
            "/api/pairs-activity",
            "/api/guardrail-alerts",
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            telemetry_error = response.json()["telemetry_error"]
            assert telemetry_error["source"] == "cycle_ring", path
            assert telemetry_error["error_type"] in {
                "IsADirectoryError", "PermissionError"
            }, path
            assert telemetry_error["error"], path
    finally:
        set_ring_override(None)


def test_api_and_html_endpoints(client, temp_db):
    """Test the FastAPI / and /api/state endpoints."""
    # Test HTML index
    res_html = client.get("/")
    assert res_html.status_code == 200
    assert "Spread Hunter" in res_html.text
    assert "/static/app.js" in res_html.text

    # Test /api/state
    res_json = client.get("/api/state")
    assert res_json.status_code == 200
    data = res_json.json()
    assert "empty" in data
    assert "capital" in data
    assert "pairs" in data


def test_pairs_activity_endpoint_aggregates_ring(client, tmp_path):
    """/api/pairs-activity counts pairs_* ring actions per cycle and per pair,
    ignoring non-pairs events and keeping each pair's most recent action.
    """
    ring = tmp_path / "cycle_events.jsonl"
    rows = [
        {"ts": "2026-08-21T02:00:01Z", "service": "engine", "cycle": 1,
         "phase": "settling", "action": "pairs_completed",
         "market_slug": "", "reason": "", "latency_ms": 0.0, "pid": 1,
         "extra": {"pair_id": "pair-A"}},
        {"ts": "2026-08-21T02:00:06Z", "service": "engine", "cycle": 1,
         "phase": "settling", "action": "pairs_would_complete",
         "market_slug": "", "reason": "", "latency_ms": 0.0, "pid": 1,
         "extra": {"pair_id": "pair-B"}},
        {"ts": "2026-08-21T02:00:11Z", "service": "engine", "cycle": 2,
         "phase": "settling", "action": "pairs_exited",
         "market_slug": "", "reason": "", "latency_ms": 0.0, "pid": 1,
         "extra": {"pair_id": "pair-A"}},
        {"ts": "2026-08-21T02:00:16Z", "service": "engine", "cycle": 2,
         "phase": "settling", "action": "pairs_error",
         "market_slug": "", "reason": "", "latency_ms": 0.0, "pid": 1,
         "extra": {"pair_id": "pair-C"}},
        {"ts": "2026-08-21T02:00:21Z", "service": "engine", "cycle": 1,
         "phase": "quoting", "action": "decide",
         "market_slug": "x", "reason": "", "latency_ms": 0.0, "pid": 1,
         "extra": {}},
    ]
    ring.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    set_ring_override(ring)
    try:
        res = client.get("/api/pairs-activity")
        assert res.status_code == 200
        data = res.json()
        assert data["last_cycle"] == 2
        assert data["last_cycle_counts"] == {"exited": 1, "error": 1}
        assert data["totals"] == {
            "completed": 1, "would_complete": 1,
            "exited": 1, "error": 1,
        }
        by_id = {p["pair_id"]: p for p in data["per_pair"]}
        assert by_id["pair-A"]["action"] == "exited"   # most recent wins
        assert by_id["pair-B"]["action"] == "would_complete"
        assert by_id["pair-C"]["action"] == "error"
        assert by_id["pair-A"]["cycle"] == 2
    finally:
        set_ring_override(None)


def test_guardrail_health_endpoint_reports_alive_watcher(client, tmp_path):
    """/api/guardrail-health reads the watcher's heartbeat: a fresh one means
    running, with pid/cycle from the file and the alert total from the ring.
    """
    ring = tmp_path / "cycle_events.jsonl"
    hb = tmp_path / "global_stop_loss_heartbeat.json"
    now = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    hb.write_text(json.dumps([{
        "pid": 4242, "started_at": now_iso, "ts": now_iso, "cycle": 9,
    }]), encoding="utf-8")
    rows = [
        {"ts": "2026-08-21T02:00:05Z", "service": "engine", "cycle": 3,
         "phase": "settling", "action": "guardrail_alert",
         "market_slug": "", "reason": "REPEAT-EXIT", "latency_ms": 0.0,
         "pid": 1, "extra": {}},
        {"ts": "2026-08-21T02:00:10Z", "service": "engine", "cycle": 4,
         "phase": "settling", "action": "guardrail_alert",
         "market_slug": "", "reason": "OVER-CAP-PAIR", "latency_ms": 0.0,
         "pid": 1, "extra": {}},
    ]
    ring.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    set_ring_override(ring)
    set_guardrail_heartbeat_override(hb)
    try:
        res = client.get("/api/guardrail-health")
        assert res.status_code == 200
        h = res.json()
        assert h["running"] is True
        assert h["pid"] == 4242
        assert h["cycle"] == 9
        assert h["age_s"] is not None and h["age_s"] <= 30.0
        assert h["alerts_total"] == 2
        assert h["last_alert_kind"] == "OVER-CAP-PAIR"  # newest first
    finally:
        set_ring_override(None)
        set_guardrail_heartbeat_override(None)


def test_guardrail_health_endpoint_flags_stale_watcher(client, tmp_path):
    """A heartbeat older than the stale threshold reads as DOWN -- the
    silent-failure case the chip exists to surface.
    """
    hb = tmp_path / "global_stop_loss_heartbeat.json"
    old = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hb.write_text(json.dumps([{
        "pid": 99, "started_at": old, "ts": old, "cycle": 3,
    }]), encoding="utf-8")
    set_guardrail_heartbeat_override(hb)
    try:
        res = client.get("/api/guardrail-health")
        assert res.status_code == 200
        h = res.json()
        assert h["running"] is False
        assert h["age_s"] is not None and h["age_s"] > 30.0
        assert h["pid"] == 99   # pid still readable; the chip says DOWN
    finally:
        set_guardrail_heartbeat_override(None)


def test_guardrail_health_endpoint_absent_heartbeat(client, tmp_path):
    """No heartbeat file at all (watcher never ran / old code) -> DOWN, empty
    fields, no crash.
    """
    empty_ring = tmp_path / "empty_ring.jsonl"
    empty_ring.write_text("", encoding="utf-8")
    set_ring_override(empty_ring)
    set_guardrail_heartbeat_override(tmp_path / "missing.json")
    try:
        res = client.get("/api/guardrail-health")
        assert res.status_code == 200
        h = res.json()
        assert h["running"] is False
        assert h["pid"] is None
        assert h["age_s"] is None
        assert h["alerts_total"] == 0
        assert h["telemetry_error"] is None
    finally:
        set_ring_override(None)
        set_guardrail_heartbeat_override(None)


def test_guardrail_alerts_endpoint_returns_newest_first(client, tmp_path):
    """/api/guardrail-alerts surfaces guardrail_alert ring events newest-first
    with kind/subject/detail mapped from reason/extra, ignoring other events.
    """
    ring = tmp_path / "cycle_events.jsonl"
    rows = [
        {"ts": "2026-08-21T02:00:05Z", "service": "engine", "cycle": 3,
         "phase": "settling", "action": "guardrail_alert",
         "market_slug": "", "reason": "REPEAT-EXIT", "latency_ms": 0.0,
         "pid": 1, "extra": {"subject": "pair-0xAAA exited twice",
                              "detail": "2 exits in window"}},
        {"ts": "2026-08-21T02:00:10Z", "service": "engine", "cycle": 4,
         "phase": "settling", "action": "guardrail_alert",
         "market_slug": "", "reason": "OVER-CAP-PAIR", "latency_ms": 0.0,
         "pid": 1, "extra": {"subject": "0x3bae865f pair $1.12",
                              "detail": "over 0.995 cap"}},
        {"ts": "2026-08-21T02:00:15Z", "service": "engine", "cycle": 4,
         "phase": "quoting", "action": "decide",
         "market_slug": "x", "reason": "", "latency_ms": 0.0, "pid": 1,
         "extra": {}},
    ]
    ring.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    set_ring_override(ring)
    try:
        res = client.get("/api/guardrail-alerts")
        assert res.status_code == 200
        alerts = res.json()["alerts"]
        assert len(alerts) == 2
        # newest first (the decide event is not an alert and is ignored)
        assert alerts[0]["kind"] == "OVER-CAP-PAIR"
        assert alerts[0]["subject"] == "0x3bae865f pair $1.12"
        assert alerts[0]["detail"] == "over 0.995 cap"
        assert alerts[0]["cycle"] == 4
        assert alerts[1]["kind"] == "REPEAT-EXIT"
        assert alerts[1]["subject"] == "pair-0xAAA exited twice"
    finally:
        set_ring_override(None)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_dashboard_script_parses():
    """Ensures no syntax errors in app.js (now extracted to static file)."""
    from pathlib import Path
    app_js = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "app.js"
    if not app_js.exists():
        pytest.skip("dashboard/static/app.js not found")
    if NODE is None:
        pytest.skip("node not installed")
    res = subprocess.run(["node", "--check", str(app_js)], capture_output=True, text=True)
    assert res.returncode == 0, f"app.js parse error: {res.stderr}"


def test_api_state_ignores_a_request_supplied_db_path(client, tmp_path):
    """The database is chosen by CLI or env only, never by the caller.

    A query parameter here let anything that could reach the port read an
    arbitrary SQLite file and probe local paths through the error text.
    """
    other = tmp_path / "somewhere_else.db"
    con = sqlite3.connect(str(other))
    con.executescript(SCHEMA)
    con.commit()
    con.close()

    res = client.get("/api/state", params={"db": str(other)})
    assert res.status_code == 200
    assert str(other) not in res.json()["db_path"]


def test_database_values_are_escaped_before_innerhtml():
    """Token ids and statuses reach innerHTML, and the venue writes some of them.
    Now checks app.js (extracted from inline PAGE_HTML).
    """
    app_js = _read_static("app.js")
    assert "function esc(v)" in app_js
    # The esc() function must exist and escape &, <, >, ", '
    assert "&amp;" in app_js
    assert "&lt;" in app_js


def test_dashboard_reads_exactly_where_the_registry_writes():
    """One live registry, one path. A dashboard aimed elsewhere reports a calm lie.

    The repo root still carries a run/live.db from before the live path was
    extracted. Preferring it pointed this page at a dead file that never
    receives a fill, which reads identically to a healthy idle cycle.
    """
    from core_brain.order_registry import DEFAULT_DB_PATH

    assert resolve_db_path() == DEFAULT_DB_PATH
    assert resolve_db_path().name == "orders.db"
    assert resolve_db_path().parent.name == "data"


def test_milestone8_html_contains_required_sections():
    """Milestone 8 requirement: UI components for 3 levels, exposure chart, and run selector exist in HTML.

    The market inspection table was retired from Data & Markets (kanban +
    dashboard orders table superseded it), so it is asserted ABSENT here.
    """
    assert "kpi-grid" in _read_static("index.html")
    assert "exposure-bar" in _read_static("index.html")
    assert "service-cards" in _read_static("index.html")
    assert "event-ticker" in _read_static("index.html")
    assert "cancel-modal" not in _read_static("index.html")
    assert "market-table" not in _read_static("index.html")
    assert "tab-switcher" in _read_static("index.html")
    assert "info-bubble" in _read_static("app.js")  # generated dynamically by JS


def test_level1_trade_analytics_tiles_are_present():
    """The new win-rate/expectancy, distribution, Sharpe, and drawdown tiles exist."""
    app_js = _read_static("app.js")
    assert "Sharpe" in app_js
    assert "Drawdown" in app_js
    assert "Win Rate" in app_js
    assert "trade_analytics" in app_js
    assert "renderKPIs" in app_js


def test_api_kpi_endpoint_returns_3_levels_and_run_isolation(client, temp_db):
    """Test /api/kpi provides Level 1 (Strategy), Level 2 (Market), Level 3 (Mechanics), and run isolation."""
    from core_brain.order_registry import (
        OrderRegistry, OrderRecord, FillRecord, QuoteRecord,
        MarketEventRecord, MarkoutRecord, CloseRecord, FloatMarkRecord,
        VenueErrorRecord, DivergenceEventRecord
    )
    reg = OrderRegistry(temp_db)
    t_now = time.time()

    # Run 1: run-first-cycle
    r1 = "run-first-cycle"
    reg.create_order(OrderRecord(
        id="ord-up", order_id="clob-up", condition_id="0xmarket1", token_id="tok-up",
        side="BUY", price=0.62, original_size=5.0, status="filled",
        posted_ts=int((t_now - 120) * 1000), last_polled_ts=int(t_now * 1000),
        pair_id="pair-1", max_pair_cost_at_post=0.95, run_id=r1
    ))
    reg.create_order(OrderRecord(
        id="ord-dn", order_id="clob-dn", condition_id="0xmarket1", token_id="tok-dn",
        side="BUY", price=0.32, original_size=5.0, status="filled",
        posted_ts=int((t_now - 120) * 1000), last_polled_ts=int(t_now * 1000),
        pair_id="pair-1", max_pair_cost_at_post=0.95, run_id=r1
    ))
    reg.log_quote(QuoteRecord(
        ts=t_now - 120, condition_id="0xmarket1", token_id="tok-up", side="UP",
        price=0.62, size=5.0, queue_ahead=8.0, mid=0.63, edge_vs_mid=0.01,
        filled=5.0, latency_ms=25.0, local_id="ord-up", run_id=r1
    ))
    reg.log_quote(QuoteRecord(
        ts=t_now - 120, condition_id="0xmarket1", token_id="tok-dn", side="DOWN",
        price=0.32, size=5.0, queue_ahead=5.0, mid=0.33, edge_vs_mid=0.01,
        filled=5.0, latency_ms=25.0, local_id="ord-dn", run_id=r1
    ))
    reg.record_fill(FillRecord(
        trade_id="tr-up", order_uuid="ord-up", size=5.0, price=0.62,
        venue_ts=int((t_now - 100) * 1000), recorded_ts=int((t_now - 99) * 1000), run_id=r1
    ))
    reg.record_fill(FillRecord(
        trade_id="tr-dn", order_uuid="ord-dn", size=5.0, price=0.32,
        venue_ts=int((t_now - 90) * 1000), recorded_ts=int((t_now - 89) * 1000), run_id=r1
    ))
    reg.log_markout(MarkoutRecord(
        ts=t_now - 100, condition_id="0xmarket1", side="UP", fill_price=0.62, size=5.0,
        ref_mid=0.63, mid_h0=0.635, mid_h1=0.64, mid_h2=0.645, mid_h3=0.638, done=1, run_id=r1
    ))
    reg.log_close(CloseRecord(
        ts=t_now - 30, condition_id="0xmarket1", method="merge", shares=5.0,
        cost_basis=4.70, proceeds=5.00, realized_pnl=0.30, tx_hash="0xhash123", run_id=r1
    ))
    reg.log_market_event(MarketEventRecord(
        ts=t_now - 130, condition_id="0xmarket1", kind="QUOTING", reason="both sides placed",
        reason_code="INTENT_GENERATED", run_id=r1
    ))
    reg.log_market_event(MarketEventRecord(
        ts=t_now - 140, condition_id="0xmarket_blocked", kind="BLOCKED", reason="outside price band",
        reason_code="PRICE_BAND", run_id=r1
    ))
    reg.log_float_mark(unrealized_usd=0.0, committed_open_usd=4.70, naked_usd=0.0, ts=t_now - 90, run_id=r1)
    reg.log_venue_error(VenueErrorRecord(
        ts=t_now - 150, condition_id="0xmarket1", side="BUY", price=0.62, size=5.0,
        error_code="INVALID_POST", raw_error_msg="post-only cross rejected", run_id=r1
    ))
    reg.log_divergence_event(DivergenceEventRecord(
        ts=t_now - 20, condition_id="0xmarket1", pair_id="pair-1",
        registry_diff=0.0, venue_diff=0.0, chain_diff=0.0, divergence_msg="all matched", run_id=r1
    ))

    # Test /api/kpi
    res = client.get("/api/kpi", params={"run_id": r1})
    assert res.status_code == 200
    data = res.json()

    # Level 1: Run level
    assert data["fills"] == 2
    assert data["filled_shares"] == 10.0
    assert data["realized_pnl"] == 0.30
    assert data["fill_rate"] == 1.0
    assert data["spread_capture"] > 0
    assert data["runs"][0]["run_id"] == r1

    # Level 2: Market level & Drilldown
    assert "0xmarket1" in data["by_market"]
    mkt = data["by_market"]["0xmarket1"]
    # The pair merged: both legs went back to the venue and came out as $1.00
    # a share, booked in `realized_pnl` above. Nothing is held, so there is no
    # pair left to price or to call balanced. Asserting 5/5 here asserted the
    # phantom -- the same shares counted once as realized and again as an open
    # position marked at par. `pair_cost` and `balance` on a pair that is still
    # held are covered by tests/test_kpi_merge_inventory.py.
    assert mkt["up_sh"] == 0.0
    assert mkt["dn_sh"] == 0.0
    assert mkt["pair_cost"] is None
    assert mkt["balance"] is None
    assert len(mkt["markouts"]) == 1
    assert mkt["markouts"][0]["mid_h0"] == 0.635
    assert mkt["markouts"][0]["mid_h1"] == 0.64
    assert mkt["markouts"][0]["mid_h2"] == 0.645
    assert mkt["markouts"][0]["mid_h3"] == 0.638

    # Funnel
    assert "funnel" in data
    assert any(f["cause"] == "PRICE_BAND" for f in data["funnel"]["filters"])

    # Level 3: Mechanics
    assert data["order_latency_ms"]["median"] == 25.0
    assert data["reconcile_lag_ms"]["median"] == 1000.0
    assert data["venue_rejects"]["total"] == 1
    assert data["venue_rejects"]["by_code"]["INVALID_POST"] == 1
    assert data["three_way_divergences"]["total"] == 1

    # Req 4: Float marks series
    assert len(data["float_marks"]) >= 1



def test_kpi_endpoint_survives_launch_by_file_path(tmp_path):
    """`python live/dash/live_dash.py` must still serve /api/kpi.

    Launching the file by path puts live/dash/ on sys.path instead of live/, so the
    lazy `from core_brain.kpi import report` inside the endpoint raised
    ModuleNotFoundError and every poll came back 500 -- invisible to this suite,
    which runs with live/ as the working directory.
    """
    project_root = Path(__file__).resolve().parent.parent
    dash_file = project_root / "dashboard" / "server.py"
    db_file = tmp_path / "live.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

    snippet = (
        "import runpy, sys\n"
        f"sys.path.insert(0, {str(dash_file.parent)!r})\n"
        f"mod = runpy.run_path({str(dash_file)!r})\n"
        "from fastapi.testclient import TestClient\n"
        f"mod['set_db_override']({str(db_file)!r})\n"
        "r = TestClient(mod['app']).get('/api/kpi')\n"
        "print(r.status_code)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("200"), proc.stdout + proc.stderr


def test_page_html_contains_status_bar_and_bot_buttons():
    """Verify the header keeps only the compact status and safe run controls."""
    html = _read_static("index.html")
    app_js = _read_static("app.js")
    assert "service-cards" in html
    assert "btn-cancel-all" not in html
    assert "cancel-modal" not in html
    assert "usdc-balance" not in html
    assert "hud-venue-mode" not in html
    assert "tab-switcher" in html
    assert "/api/system/start" in app_js
    assert "/api/system/stop" in app_js
    assert "pill" in app_js


def test_system_status_endpoint(client):
    """GET /api/system/status returns exactly 4 sub-services, and bot state.

    The key set is compared exactly rather than key by key. The rename replaced
    `screener`/`engine`/`fleet` with `filter`/`query`/`decide`, and a response
    still carrying one of the legacy keys passes a presence-only check while
    the dashboard renders a service that no longer runs.
    """
    res = client.get("/api/system/status")
    assert res.status_code == 200
    data = res.json()
    assert "services" in data
    assert set(data["services"]) == {"filter", "query", "decide", "dash"}
    assert data["services"]["dash"]["running"] is True
    assert "bot_state" in data


def test_system_start_and_stop_endpoints(client, monkeypatch, tmp_path):
    """POST /api/system/start and POST /api/system/stop control bot state safely.

    start_bot() Popens the real screener and the real `live_exec poll` loop. A
    child process does not inherit conftest's socket guard, and live_exec loads
    dotenv at import, so an unmocked call here signs real requests to the venue
    from a test run -- and nothing in the test would ever stop the loops. The
    spawn is stubbed; what is under test is the endpoint contract.
    """
    import dashboard.server as dash_mod

    spawned = []
    monkeypatch.setattr(
        dash_mod, "start_bot",
        lambda: (spawned.append("start"), {"ok": True, "message": "stubbed"})[1],
    )
    monkeypatch.setattr(
        dash_mod, "stop_bot",
        lambda: (spawned.append("stop"), {"ok": True, "message": "stubbed"})[1],
    )

    res_stop = client.post("/api/system/stop", headers=_control(client))
    assert res_stop.status_code == 200
    assert res_stop.json().get("ok") is True

    res_start = client.post("/api/system/start", headers=_control(client))
    assert res_start.status_code == 200
    assert res_start.json().get("ok") is True

    assert spawned == ["stop", "start"]


def test_start_endpoint_never_spawns_a_process_under_test(client, monkeypatch):
    """A regression guard: no test may Popen the live bot stack.

    Fails against the unpatched suite, where /api/system/start reached
    subprocess.Popen and left `scripts.rerank_loop` and `engine.order_manager poll`
    running against the venue after pytest exited.
    """
    import subprocess

    import dashboard.server as dash_mod

    def _forbidden(*args, **kwargs):
        raise AssertionError(f"test spawned a live process: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(
        dash_mod, "start_bot", lambda: {"ok": True, "message": "stubbed"}
    )

    res = client.post("/api/system/start", headers=_control(client))
    assert res.status_code == 200


def test_reset_db_refuses_to_destroy_an_archived_run(tmp_path, monkeypatch):
    """Reset must not delete an archive opened for reading.

    reset_database archives-then-unlinks whatever --db points at. Launched
    against a past cycle for a post-mortem, the unguarded version destroyed the
    record the operator opened the page to read, and nested a fresh archive/
    inside the archive directory on the way out.
    """
    import dashboard.server as dash_mod
    # Isolate the bot-running gate (see test_system_reset_db_endpoint): point
    # LIVE_ROOT at tmp_path so the global live_procs.json is not consulted.
    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    from dashboard.server import reset_database

    archive_dir = tmp_path / "runtime" / "archive"
    archive_dir.mkdir(parents=True)
    archived = archive_dir / "live_20260819_090708.db"
    con = sqlite3.connect(str(archived))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    size_before = archived.stat().st_size

    result = reset_database(archived)

    assert result["ok"] is False
    assert "archived run" in result["message"]
    assert archived.exists(), "the archived cycle was deleted"
    assert archived.stat().st_size == size_before
    assert not (archive_dir / "archive").exists(), "nested archive/ was created"


def test_system_restart_dash_endpoint(client, monkeypatch):
    """POST /api/system/restart-dash launches a replacement and exits this one.

    Two things must be neutralised or the suite dies: the handler's background
    thread really calls os._exit(0) after 0.8s, which would kill pytest itself,
    and it really Popens a second dashboard. Both are patched at the module the
    handler resolves them through. `threading.Thread.start` is deliberately NOT
    patched -- TestClient runs each request on its own portal thread, so a no-op
    start deadlocks the client instead of testing anything.
    """
    import subprocess

    import dashboard.server as dash_mod

    launched = []
    exited = []

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: launched.append(a))
    monkeypatch.setattr(dash_mod.os, "_exit", lambda code: exited.append(code))

    res = client.post("/api/system/restart-dash", headers=_control(client))
    assert res.status_code == 200
    assert res.json().get("ok") is True
    # The restart-dash route exists in the backend
    from dashboard.server import app as dash_app
    assert any(getattr(r, 'path', '') == '/api/system/restart-dash' for r in dash_app.routes)

    # The handler's daemon thread sleeps 0.8s before acting. Wait for it here:
    # if monkeypatch tore down first, the real os._exit(0) would kill pytest.
    deadline = time.time() + 5.0
    while not exited and time.time() < deadline:
        time.sleep(0.05)

    assert launched, "no replacement dashboard was launched"
    assert exited == [0], "the old instance did not exit to release the port"


def test_restart_relaunches_by_absolute_path(monkeypatch):
    """The replacement dashboard must be launched by absolute path.

    sys.argv[0] is whatever the operator typed, and .claude/launch.json types it
    relative ("live/dash/live_dash.py"). Replaying that under cwd=live/ looks for
    live/live/dash/live_dash.py, so the replacement dies on startup and the page
    never comes back -- while the current instance has already os._exit(0)'d.
    """
    from dashboard.server import LIVE_ROOT, relaunch_argv

    # Exactly how launch.json invokes it, from the repo root.
    monkeypatch.setattr(sys, "argv", ["live/dash/live_dash.py", "--port", "8799"])

    argv = relaunch_argv()
    script = Path(argv[1])

    assert script.is_absolute(), f"relaunched by relative path: {script}"
    assert script.exists(), f"relaunch target does not exist: {script}"
    assert script.name in ("server.py", "live_dash.py")
    # The restart runs with cwd=LIVE_ROOT; the command must still resolve there.
    assert (LIVE_ROOT / script).exists()
    # Port and database flags survive the restart.
    assert argv[2:] == ["--port", "8799"]


def test_restart_preserves_the_database_flag(monkeypatch):
    """A restart must come back on the same database it was reading."""
    from dashboard.server import relaunch_argv

    monkeypatch.setattr(sys, "argv", ["live_dash.py", "--db", "runtime/archive/live_x.db"])
    assert relaunch_argv()[2:] == ["--db", "runtime/archive/live_x.db"]


def test_system_reset_db_endpoint(client, temp_db, tmp_path, monkeypatch):
    """POST /api/system/reset-db archives the existing DB and initializes a clean fresh DB."""
    import sqlite3
    import dashboard.server as dash_mod
    # Isolate the bot-running gate: reset_database() consults the global
    # LIVE_ROOT/run/live_procs.json to decide if the bot is RUNNING. Point
    # LIVE_ROOT at the test tmp so the gate sees no procs file and treats the
    # bot as STOPPED, otherwise a live bot on the operator's machine short-
    # circuits the reset before it ever touches temp_db.
    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    # Put a dummy row in temp_db first
    conn = sqlite3.connect(temp_db)
    conn.execute("INSERT INTO orders (id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts) VALUES ('dummy-1', '0x1', '0x2', 'BUY', 0.5, 10, 'open', 1000, 1000)")
    conn.commit()
    conn.close()

    res = client.post("/api/system/reset-db", headers=_control(client))
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") is True
    assert "archived_to" in data
    archived_path = data["archived_to"]

    # Verify orders table in freshly created DB is empty
    conn2 = sqlite3.connect(temp_db)
    cursor = conn2.cursor()
    cursor.execute("SELECT COUNT(*) FROM orders")
    count = cursor.fetchone()[0]
    conn2.close()
    assert count == 0

    # Verify the archive file is a valid sqlite3 db containing the original pre-reset data.
    # reset_database() returns only the archive *filename*; the file lives under
    # <target_db.parent>/archive/, and target_db resolves to temp_db (tmp_path/live.db).
    archive_dir = tmp_path / "archive"
    arch_conn = sqlite3.connect(archive_dir / archived_path)
    arch_cursor = arch_conn.cursor()
    arch_cursor.execute("SELECT id FROM orders WHERE id = 'dummy-1'")
    archived_row = arch_cursor.fetchone()
    arch_conn.close()
    assert archived_row is not None, "Original dummy-1 row must be preserved in archive"
    assert archived_row[0] == "dummy-1"




# --------------------------------------------------------------------------
# CodeRabbit review round on PR #44
# --------------------------------------------------------------------------

def test_active_orders_panel_js_filter_present():
    """The 'Active Pair Orders' panel must hide filled/cancelled rows client-side.

    The state half of this regression moved to test_registry_state.py
    (test_state_returns_all_orders_including_terminal). The Data & Markets
    inspection table was retired (the kanban + the dashboard's orders table
    superseded it), so the template contract here is only that the orders
    table renderer and its client-side filter still exist.
    """
    assert "renderOrdersTrades" in _read_static("app.js")
    assert "isActiveOrder" in _read_static("app.js")


def test_api_state_does_not_pre_filter_orders(client, temp_db):
    """The server emits ALL orders; the JS filter is what renders only
    active ones. A server-side filter would be brittle (anyone calling
    /api/state from a tool would see a partial picture).
    """
    now_ms = int(time.time() * 1000)
    con = sqlite3.connect(str(temp_db))
    con.executemany(
        """INSERT INTO orders (id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts)
           VALUES (?, 'cond-1', 'tok-1', 'BUY', 0.5, 10.0, ?, ?, ?)""",
        [
            ("ord-active-1", "open", now_ms, now_ms),
            ("ord-active-2", "pending", now_ms, now_ms),
            ("ord-active-3", "partial", now_ms, now_ms),
            ("ord-cancelled", "cancelled", now_ms, now_ms),
            ("ord-filled", "filled", now_ms, now_ms),
        ],
    )
    con.commit()
    con.close()

    res = client.get("/api/state")
    assert res.status_code == 200
    data = res.json()
    ids = {o["id"] for o in data["orders"]}
    # All five rows on the wire; the JS hides three of them in the table.
    assert ids == {"ord-active-1", "ord-active-2", "ord-active-3",
                   "ord-cancelled", "ord-filled"}


def _control(client):
    """Headers that authorize a machine-state change from this process's page."""
    from dashboard.server import CONTROL_TOKEN
    return {"X-Control-Token": CONTROL_TOKEN}


def test_control_endpoints_reject_untokened_posts(client):
    """A POST without the page's token must not change machine state.

    Loopback binding is not a defence: a page in the operator's browser can
    submit a cross-origin form POST to 127.0.0.1:8799 with no CORS preflight,
    and /api/system/start spawns the loop that signs real venue requests.
    """
    for path in (
        "/api/system/start",
        "/api/system/stop",
        "/api/system/reset-db",
        "/api/system/restart-dash",
        "/api/system/sweep-interval",
    ):
        assert client.post(path).status_code == 403, f"{path} accepted an untokened POST"


def test_control_endpoints_reject_foreign_origin(client, monkeypatch):
    """Even with a token, a request claiming a foreign Origin is refused."""
    import dashboard.server as dash_mod

    monkeypatch.setattr(dash_mod, "start_bot", lambda: {"ok": True})
    res = client.post(
        "/api/system/start",
        headers={**_control(client), "Origin": "https://evil.example"},
    )
    assert res.status_code == 403


def test_page_carries_a_live_token_but_the_constant_does_not(client):
    """The served page gets a real token; PAGE_HTML keeps only the placeholder."""
    from dashboard.server import CONTROL_TOKEN, CONTROL_TOKEN_PLACEHOLDER

    html = _read_static("index.html")
    assert CONTROL_TOKEN_PLACEHOLDER in html
    assert CONTROL_TOKEN not in html

    body = client.get("/").text
    assert CONTROL_TOKEN in body
    assert CONTROL_TOKEN_PLACEHOLDER not in body


def test_start_refuses_a_second_bot_stack(client, monkeypatch):
    """Two stacks on one database sum independent inventories into invalid data.

    live_procs.json only remembers the newest PIDs, so a second start would also
    strand the first pair beyond the reach of stop_bot.
    """
    import subprocess

    import dashboard.server as dash_mod

    # The `client` fixture points the page at a temp database, which START now
    # refuses on its own (a shadow view may not launch a live stack). Clear the
    # override so what is under test here is the second-stack guard; the fixture
    # clears it again on teardown. Not monkeypatch: its restore would put the
    # temp path back after the fixture has already reset it.
    dash_mod.set_db_override(None)
    monkeypatch.setattr(
        dash_mod, "get_system_status", lambda: {"bot_state": "RUNNING"}
    )
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *a, **kw: pytest.fail("a second bot stack was spawned"),
    )

    res = client.post("/api/system/start", headers=_control(client))
    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert "already running" in res.json()["message"]


def test_reset_db_refuses_while_the_bot_is_running(monkeypatch, temp_db):
    """Unlinking the registry under a live writer loses every later write."""
    import dashboard.server as dash_mod

    monkeypatch.setattr(dash_mod, "get_system_status", lambda: {"bot_state": "RUNNING"})
    result = dash_mod.reset_database(temp_db)

    assert result["ok"] is False
    assert "running" in result["message"].lower()
    assert temp_db.exists(), "the live registry was deleted under the bot"


def test_status_reports_the_port_actually_bound(monkeypatch):
    """--port moves the dashboard, and the status payload must follow it."""
    import dashboard.server as dash_mod

    monkeypatch.setattr(dash_mod, "_ACTIVE_PORT", 9123)
    assert dash_mod.get_system_status()["services"]["dash"]["port"] == 9123
    # The page reads the reported port instead of a literal.
    assert "':8799'" not in _read_static('index.html')


def test_sweep_interval_is_configurable_from_env(monkeypatch):
    """LIVE_SWEEP_INTERVAL sets the card's cadence; absent/bad values don't."""
    from dashboard.server import resolve_sweep_interval

    monkeypatch.setenv("LIVE_SWEEP_INTERVAL", "30")
    assert resolve_sweep_interval() == 30.0

    monkeypatch.delenv("LIVE_SWEEP_INTERVAL")
    assert resolve_sweep_interval() is None

    monkeypatch.setenv("LIVE_SWEEP_INTERVAL", "garbage")
    assert resolve_sweep_interval() is None

    monkeypatch.setenv("LIVE_SWEEP_INTERVAL", "-5")
    assert resolve_sweep_interval() is None


def test_status_surfaces_engine_sweep_cadence(monkeypatch, tmp_path):
    """The system-status payload reports how often query sweeps."""
    import dashboard.server as dash_mod

    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    monkeypatch.setenv("LIVE_SWEEP_INTERVAL", "30")
    assert dash_mod.get_system_status()["services"]["query"]["sweep_interval_sec"] == 30.0

    monkeypatch.delenv("LIVE_SWEEP_INTERVAL")
    assert dash_mod.get_system_status()["services"]["query"]["sweep_interval_sec"] is None


def test_start_bot_passes_sweep_interval_to_poll(monkeypatch, tmp_path):
    """start_bot launches poll with --sweep-interval when one is configured."""
    import subprocess

    import dashboard.server as dash_mod

    spawned = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            spawned.append(args)
            self.pid = 12345

    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    monkeypatch.setattr(dash_mod, "get_system_status", lambda: {"bot_state": "STOPPED"})
    monkeypatch.setattr(dash_mod, "resolve_sweep_interval", lambda: 30.0)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    result = dash_mod.start_bot()
    assert result["ok"] is True

    engine_cmd = next(a for a in spawned if "poll" in a)
    assert "--sweep-interval" in engine_cmd
    # Compact :g formatting from _start_stack_commands (single source for
    # start_bot and the preflight preview); float("30") == float("30.0").
    assert engine_cmd[engine_cmd.index("--sweep-interval") + 1] == "30"


def test_start_bot_spawns_screener_engine_and_fleet(monkeypatch, tmp_path):
    """Start Bot launches the full hands-off stack: rerank, poll, and fleet.

    The fleet loop must not reconcile or sweep -- poll owns both -- so its
    command line carries --no-reconcile and --no-sweep alongside --live.
    """
    import subprocess

    import dashboard.server as dash_mod

    spawned = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            spawned.append(args)
            self.pid = 12345

    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    monkeypatch.setattr(dash_mod, "get_system_status", lambda: {"bot_state": "STOPPED"})
    monkeypatch.setattr(dash_mod, "resolve_sweep_interval", lambda: None)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    result = dash_mod.start_bot()
    assert result["ok"] is True

    cmds = [" ".join(a) for a in spawned]
    assert any("scripts.filter_loop" in c or "scripts.rerank_loop" in c for c in cmds), cmds
    assert any("core_brain.order_manager" in c and "poll" in c for c in cmds), cmds

    fleet_cmd = next(c for c in cmds if "core_brain.trader_loop" in c)
    assert "--live" in fleet_cmd
    assert "--no-reconcile" in fleet_cmd
    assert "--no-sweep" in fleet_cmd


def test_start_bot_decide_loop_has_no_market_cap(monkeypatch, tmp_path):
    """The Decide loop must quote every market the filter graduates.

    The old `--max-markets 1` dashboard cap let one market through while the
    filter graduated several; the trader's own default is already "all
    markets", so the dashboard must not pass the flag at all (#194).
    """
    import subprocess

    import dashboard.server as dash_mod

    spawned = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            spawned.append(args)
            self.pid = 12345

    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    monkeypatch.setattr(dash_mod, "get_system_status", lambda: {"bot_state": "STOPPED"})
    monkeypatch.setattr(dash_mod, "resolve_sweep_interval", lambda: None)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    result = dash_mod.start_bot()
    assert result["ok"] is True

    fleet_cmds = [" ".join(a) for a in spawned if "core_brain.trader_loop" in " ".join(a)]
    assert fleet_cmds, spawned
    for cmd in fleet_cmds:
        assert "--max-markets" not in cmd, (
            "dashboard must not cap the Decide loop's market rotation (#194): " + cmd
        )


def test_set_sweep_interval_persists_and_applies(monkeypatch, tmp_path):
    """The control writes LIVE_SWEEP_INTERVAL into .env and the status payload."""
    import os

    import dashboard.server as dash_mod

    env_file = tmp_path / ".env"
    env_file.write_text("POLY_PRIVATE_KEY=do-not-load\nOTHER=1\n", encoding="utf-8")
    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    monkeypatch.delenv("LIVE_SWEEP_INTERVAL", raising=False)

    result = dash_mod.set_sweep_interval("45")
    assert result["ok"] is True
    assert result["sweep_interval_sec"] == 45.0
    assert os.environ["LIVE_SWEEP_INTERVAL"] == "45.0"

    saved = env_file.read_text(encoding="utf-8")
    assert "LIVE_SWEEP_INTERVAL=45" in saved
    # Everything else in the file survives, credentials included.
    assert "POLY_PRIVATE_KEY=do-not-load" in saved
    assert "OTHER=1" in saved
    assert result["status"]["services"]["query"]["sweep_interval_sec"] == 45.0


def test_set_sweep_interval_rejects_bad_values(monkeypatch, tmp_path):
    """A non-numeric or non-positive cadence is refused before touching .env."""
    import dashboard.server as dash_mod

    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)

    assert dash_mod.set_sweep_interval("abc")["ok"] is False
    assert dash_mod.set_sweep_interval("0")["ok"] is False
    assert dash_mod.set_sweep_interval("-3")["ok"] is False


def test_sweep_interval_endpoint_sets_and_clears(client, monkeypatch, tmp_path):
    """POST /api/system/sweep-interval persists and clears the cadence."""
    import dashboard.server as dash_mod

    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    monkeypatch.delenv("LIVE_SWEEP_INTERVAL", raising=False)

    res = client.post("/api/system/sweep-interval?seconds=60", headers=_control(client))
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["sweep_interval_sec"] == 60.0
    assert "LIVE_SWEEP_INTERVAL=60" in env_file.read_text(encoding="utf-8")

    res = client.post("/api/system/sweep-interval", headers=_control(client))
    data = res.json()
    assert data["ok"] is True
    assert data["sweep_interval_sec"] is None
    assert "LIVE_SWEEP_INTERVAL" not in env_file.read_text(encoding="utf-8")


def test_status_distinguishes_configured_from_running_sweep_cadence(monkeypatch, tmp_path):
    """Configured cadence is what the control set; running is what poll launched with."""
    import json
    import os
    import time

    import dashboard.server as dash_mod

    run_dir = tmp_path / "runtime"
    run_dir.mkdir()
    (run_dir / "processes.json").write_text(json.dumps({
        "query": {"pid": os.getpid(), "started_at": time.time(), "sweep_interval_sec": 30.0},
    }), encoding="utf-8")

    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    monkeypatch.setattr(dash_mod, "_is_pid_alive", lambda pid, started_at=None: pid is not None)
    monkeypatch.setenv("LIVE_SWEEP_INTERVAL", "60")

    query = dash_mod.get_system_status()["services"]["query"]
    assert query["sweep_interval_sec"] == 60.0           # configured
    assert query["running_sweep_interval_sec"] == 30.0   # running process's launch value


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
        try:
            # Bound each next() call with a timeout
            import signal
            def timeout_handler(signum, frame):
                raise TimeoutError("next() timed out")
            if hasattr(signal, 'SIGALRM'):
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(1)
            line = next(gen)
            if hasattr(signal, 'SIGALRM'):
                signal.alarm(0)
            if '"action": "decide"' in line:
                saw_follow = True
                break
        except (TimeoutError, StopIteration):
            break
    try:
        gen.close()
    except Exception:
        pass
    assert saw_follow


def test_cycle_stream_sse_detects_rotation_by_file_identity(tmp_path):
    """A rotation that replaces the ring with a LARGER file is still detected."""
    ring = tmp_path / "cycle_events.jsonl"
    ring.write_text(
        json.dumps({"service": "engine", "phase": "scanning", "action": "a"}) + "\n",
        encoding="utf-8",
    )
    gen = _cycle_stream_sse(ring, tail=50, poll_sec=0.01)
    next(gen)  # consume the initial replay

    # Simulate the engine's os.replace rotation with a larger-byte file.
    tmp = tmp_path / "new.jsonl"
    tmp.write_text(
        json.dumps({"service": "fleet", "phase": "quoting", "action": "b",
                    "reason": "x" * 200}) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, ring)

    deadline = time.time() + 3.0
    saw_rotate = False
    saw_new = False
    while time.time() < deadline:
        frame = next(gen)
        if frame.startswith("event: rotate"):
            saw_rotate = True
        if '"action": "b"' in frame:
            saw_new = True
        if saw_rotate and saw_new:
            break
    gen.close()
    assert saw_rotate
    assert saw_new


def test_page_html_contains_bot_brains_panel():
    """The page ships the Bot Brains panel shell and its SSE hookup."""
    assert "event-ticker" in _read_static("index.html")
    assert "/api/cycle-stream" in _read_static("app.js") or "/api/cycle-stream" in _read_static("index.html")
    assert "EventSource" in _read_static("app.js")
    assert "event-ticker" in _read_static("index.html")
    assert "/api/cycle-stream" in _read_static("app.js")


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
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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


# ── Expandable market rows — click to inspect individual orders ──

def test_app_js_has_expandable_market_rows():
    """The market table JS supports clicking a row to expand and show individual orders."""
    app_js = _read_static("app.js")
    # The expand/collapse mechanism
    assert "expandedMarkets" in app_js
    assert "market-row" in app_js
    assert "aria-expanded" in app_js
    # The per-order sub-table renderer
    assert "renderExpandedOrders" in app_js
    assert "orders-subtable" in app_js
    # Individual order fields surfaced — Remaining removed per UX (Size vs Filled only)
    assert "original_size" in app_js
    assert "size_matched" in app_js
    # size_remaining still calculated in registry_state but not rendered in expanded table (Age | Pair ID | Outcome | Price | Size | Filled | Status)
    assert "Pair ID" in app_js
    assert "pair_id" in app_js
    # Side coloring (template literal produces side-buy/side-sell at runtime)
    assert "side-" in app_js
    assert "toLowerCase" in app_js
    # Chevron indicator
    assert "expand-chevron" in app_js
    # Keyboard accessibility
    assert "keydown" in app_js

    # Cancelled orders are hidden by default with toggle
    assert "showCancelledByMarket" in app_js
    assert "isCancelledStatus" in app_js
    assert "toggle-cancelled-btn" in app_js
    assert "cancelled-count" in app_js
    assert "activeOrders" in app_js or "active order" in app_js

    # Event ticker human-readable translations
    assert "EVENT_TRANSLATIONS" in app_js
    assert "translateEvent" in app_js
    assert "ticker-translation" in app_js
    assert "ticker-raw" in app_js
    # Spot-check a few translations exist
    assert "reconcile_ok" in app_js
    assert "sweep_done" in app_js
    assert "pairs_balanced" in app_js
    assert "rerank_done" in app_js
    assert "guardrail_alert" in app_js
    assert "market_error" in app_js


def test_styles_css_has_expandable_row_styles():
    """CSS has styles for expandable market rows, chevron, sub-table, and side badges."""
    css = _read_static("styles.css")
    assert ".market-row" in css
    assert ".expand-chevron" in css
    assert ".orders-expand-row" in css
    assert ".orders-subtable" in css
    assert ".order-count-badge" in css
    assert ".side-buy" in css
    assert ".side-sell" in css


def test_market_table_has_clickable_role(tmp_path):
    """Retired with the Data & Markets inspection table. The expandable-row
    behaviour it pinned lives on in the Orders & Trades CLOSED TRADES view,
    which reuses the same row shape; test_app_js_has_expandable_market_rows
    pins that, and the kanban covers the market pipeline view.
    """
    html = _read_static("index.html")
    assert "market-table" not in html
    assert "market-body" not in html
    assert "market-inspection-card" not in html
    # The renderer itself is gone from app.js too.
    assert "function renderMarkets(" not in _read_static("app.js")


# ── Screener kanban tab (Tab 3) ──

def test_html_has_market_filter_tab_button():
    """The HTML has a 3rd tab button for the Market Filter.

    Named for the component, not the retired word: `docs/agents/glossary.md`
    replaced "screener" with **Market Filter** everywhere, operator-facing
    copy included.
    """
    html = _read_static("index.html")
    assert 'tab-btn-3' in html
    assert 'MARKET FILTER' in html
    assert 'SCREENER' not in html
    assert 'tab-3' in html
    assert 'role="tab"' in html
    assert 'aria-controls="tab-3"' in html


def test_html_has_kanban_board_container():
    """The HTML has the kanban board container and carousel navigation arrows."""
    html = _read_static("index.html")
    assert 'kanban-board' in html
    assert 'kanban-carousel-container' in html
    assert 'kanban-nav-prev' in html
    assert 'kanban-nav-next' in html
    assert 'screener-header' in html
    assert 'scan-state-pill' in html
    assert 'scan-snapshot-age' in html
    # The census and gate copy were removed (Owner 2026-09-16): reference text
    # that never changed a decision at a glance. The kanban below already shows
    # what each gate refused. See tests/test_live_state_language.py.


def test_scan_state_pill_in_top_nav_bar():
    """#scan-state-pill is positioned inside the top navigation meta header (#225)."""
    html = _read_static("index.html")
    top_meta = html.split('<div class="top-meta"', 1)[1].split('</header>', 1)[0]
    assert 'id="scan-state-pill"' in top_meta
    assert 'id="market-scan-pill"' in top_meta
    assert 'id="db-mode-badge"' in top_meta
    assert 'id="usdc-balance"' not in top_meta
    assert 'id="hud-venue-mode"' not in top_meta
    assert 'id="btn-cancel-all"' not in top_meta

    # Screener header retains its label and snapshot age without breaking
    screener_hdr = html.split('id="screener-header"', 1)[1].split('</div>\n    </div>', 1)[0]
    assert 'ENGINE' in screener_hdr
    assert 'TRADING LOOP' not in screener_hdr
    assert 'scan-snapshot-age' in screener_hdr


def test_operational_statuses_share_one_top_nav_bar():
    """Operational pills and controls share one compact top navigation bar."""
    html = _read_static("index.html")
    assert "</header>" in html
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    top_meta = header.split('<div class="top-meta"', 1)[1]

    # The former second-row console is gone; the top nav owns the at-a-glance
    # operational status surface instead.
    assert 'class="live-ops-console"' not in header
    assert 'role="group" aria-label="Dashboard status and controls"' in top_meta
    assert 'id="master-status-indicator" class="pill state-unknown' in top_meta
    assert 'id="hud-services-pill"' in top_meta
    assert 'id="hud-guardrail-pill"' in top_meta
    assert 'class="top-status-pill"' not in top_meta
    assert 'class="top-status-context"' not in top_meta

    css = _read_static("styles.css")
    assert '.pill-label' in css
    assert '.pill-detail' in css

    moved_ids = (
        "master-status-indicator",
        "hud-services-pill",
        "hud-services-state",
        "hud-services-sub",
        "hud-guardrail-pill",
        "hud-guardrail-state",
        "hud-guardrail-sub",
        "btn-master-start",
        "btn-master-stop",
    )
    for element_id in moved_ids:
        assert f'id="{element_id}"' in top_meta
        assert html.count(f'id="{element_id}"') == 1

    assert 'id="btn-sync"' in top_meta
    assert ">SYNC VENUE<" in top_meta
    assert "btn-live-sync" not in html
    assert "live-ops-master-card" not in html
    assert "hud-db-mode" not in html
    # Milestone 8 guard: the kpi-grid string must survive the hero removal.
    assert 'id="kpi-grid"' in html

    # The removed IDs must not be referenced by the client logic either.
    app_js = _read_static("app.js")
    assert "btn-live-sync" not in app_js
    assert "live-ops-master-card" not in app_js
    assert "hud-db-mode" not in app_js
    assert "hud-db-sub" not in app_js
    assert "hud-venue-mode" not in app_js
    assert "usdc-balance" not in app_js
    assert "btn-cancel-all" not in app_js
    assert "cancel-modal" not in app_js


def test_app_js_surfaces_telemetry_errors_as_unknown():
    """Telemetry failures must not render as healthy or merely stopped."""
    app_js = _read_static("app.js")

    assert "scanState.telemetry_error" in app_js
    assert "Telemetry unavailable:" in app_js
    assert "title=\"Telemetry unavailable: ${esc(telemetryError.error || 'cycle ring read failed')}\"" in app_js
    assert "headerPill.title = `Telemetry unavailable: ${telemetryError.error || 'cycle ring read failed'}`" in app_js
    assert "guardrailHealth?.telemetry_error" in app_js
    assert "state-unknown" in app_js


def test_app_js_formats_heartbeat_duration_as_minutes():
    """app.js formats heartbeat duration over 60s as minutes and provides tooltip (#225)."""
    app_js = _read_static("app.js")
    assert 'formatHeartbeatAge' in app_js
    assert 'renderScanStatePill' in app_js
    assert "Math.floor(s / 60) + 'm'" in app_js or "Math.floor(sec / 60) + 'm'" in app_js
    assert 'Quote engine heartbeat:' in app_js


def test_app_js_has_render_screener():
    """app.js has the renderScreener function and kanban bucket logic."""
    app_js = _read_static("app.js")
    assert 'renderScreener' in app_js
    assert 'BUCKET_DEFS' in app_js
    assert 'kanban-board' in app_js
    assert 'kanban-bucket' in app_js
    assert 'market-card' in app_js
    # All 7 buckets defined
    assert 'raw' in app_js
    assert 'identity' in app_js
    assert 'volume' in app_js
    assert 'depth' in app_js
    assert 'spread' in app_js
    assert 'horizon' in app_js
    assert 'passed' in app_js
    # Funnel data source
    assert 'funnel' in app_js
    assert 'raw_count' in app_js
    assert 'graduated' in app_js
    assert 'filters' in app_js
    assert 'snapshot_age' in app_js
    # Scan state integration
    assert 'scan_state' in app_js
    assert 'scanState' in app_js
    # Empty state for missing pipeline
    assert 'No Market Filter data yet' in app_js
    # Near-miss footer
    assert 'would_fund' in app_js or 'would_clear' in app_js
    # Categorization and flow progression
    assert 'categorizeGate' in app_js
    assert 'stageFlow' in app_js
    assert 'getStageHero' in app_js
    assert 'kanban-bucket-hero' in app_js
    assert 'passed-card' in app_js
    assert 'card-metrics-grid' in app_js
    # Carousel navigation
    assert 'scrollKanban' in app_js
    assert 'updateKanbanNavButtons' in app_js
    assert 'initKanbanCarousel' in app_js
    assert 'ArrowLeft' in app_js
    assert 'ArrowRight' in app_js
    # 3-tab switching (tab-btn-3 is constructed dynamically)
    assert 'tab3' in app_js
    assert "'tab-btn-' + which" in app_js or 'tab-btn-1' in app_js


def test_app_js_reads_every_gate_bar_from_the_funnel():
    """No gate threshold is hardcoded in the hero blocks.

    The panel that states the money gates has to state the ones the screener
    actually ran, so a config change cannot leave it claiming an old number.
    """
    app_js = _read_static("app.js")
    for key in ('volume_gate_usd', 'depth_gate_usd', 'spread_gate',
                'horizon_gate_days', 'reward_min_income_usd_day',
                'spread_min_income_usd_day', 'max_pair_cost'):
        assert key in app_js, key
    # The literals these replaced must be gone.
    assert '0.0600 (6.00%)' not in app_js
    assert '30.0 days' not in app_js
    assert '$1.50/day' not in app_js
    assert '$0.995' not in app_js


def test_app_js_states_the_payout_floor_per_market_source():
    """One universal payout bar would call a passing spread market a failure."""
    app_js = _read_static("app.js")
    assert 'rewardIncome' in app_js
    assert 'spreadIncome' in app_js
    assert 'rewards ≥ $' in app_js or 'rewards ≥ $' in app_js
    assert 'spread > $' in app_js


def test_app_js_does_not_fabricate_sequential_gate_flow():
    """The ranker stops at the first failure and not in board order.

    Subtracting each gate's rejections from a running pool would report a
    market refused on depth as having passed volume, so every advanced count
    after the first gate would be invented. Each stage states its own refusals
    against the scored total instead.
    """
    app_js = _read_static("app.js")
    assert 'flow.rejected' in app_js
    assert 'flow.scored' in app_js
    # The running-pool arithmetic and every claim built on it are gone.
    assert 'currentPool' not in app_js
    assert 'flow.out' not in app_js
    assert 'flow.in' not in app_js
    assert 'markets advanced' not in app_js
    assert 'advanced to next stage' not in app_js


def test_app_js_suppresses_an_eligible_card_already_shown_as_quoting():
    """A market quoting on the venue must appear once, not twice."""
    app_js = _read_static("app.js")
    assert 'renderedCids' in app_js
    assert 'el.cid || el.condition_id' in app_js
    assert 'renderedCids.has(cid)' in app_js


def test_app_js_preserves_kanban_scroll_across_renders():
    """A poll must not yank a scrolled operator back to the first stage."""
    app_js = _read_static("app.js")
    assert 'prevScrollLeft' in app_js
    assert 'board.scrollLeft = prevScrollLeft' in app_js
    # One assignment, not seven reparses: `board.innerHTML +=` is gone.
    assert 'board.innerHTML +=' not in app_js
    assert 'boardHtml' in app_js


def test_app_js_never_appends_to_innerhtml_in_a_loop():
    """`el.innerHTML +=` re-serializes and re-parses the whole accumulated
    subtree on every append. With 366 market rows (~348KB of table HTML)
    that is ~63MB of parsing per render — the Data & Markets page switch
    froze ~3s on it (issue #270). Collections are built into a plain string
    and assigned to `innerHTML` exactly once."""
    app_js = _read_static("app.js")
    assert '.innerHTML +=' not in app_js


def test_app_js_escapes_snapshot_derived_values():
    """Snapshot fields are escaped before they reach the page."""
    app_js = _read_static("app.js")
    assert 'esc(m.days_to_resolve)' in app_js
    assert 'esc(m.return_pct_day)' in app_js
    assert 'esc(el.ret_day_pct)' in app_js
    assert 'esc(r.days)' in app_js
    assert 'esc(s.days)' in app_js
    assert 'aria-label="${esc(def.name)}"' in app_js


def test_app_js_honours_reduced_motion_when_scrolling():
    """An explicit scroll behavior overrides the CSS, so JS checks too."""
    app_js = _read_static("app.js")
    assert 'prefers-reduced-motion: reduce' in app_js
    assert "reduceMotion ? 'auto' : 'smooth'" in app_js


def test_styles_css_has_kanban_styles():
    """CSS has styles for the kanban board, buckets, cards, and badges."""
    css = _read_static("styles.css")
    assert '.kanban-carousel-container' in css
    assert '.kanban-board' in css
    assert '.kanban-nav-btn' in css
    assert '.kanban-bucket' in css
    assert '.kanban-bucket-header' in css
    assert '.kanban-bucket-hero' in css
    assert '.hero-param-label' in css
    assert '.hero-critical-val' in css
    assert '.kanban-bucket-body' in css
    assert '.market-card' in css
    assert '.passed-card' in css
    assert '.bucket-count-badge' in css
    assert '.kanban-empty' in css
    assert '.kanban-header-flow' in css
    # Bucket color states
    assert '.kanban-bucket.passed' in css
    assert '.kanban-bucket.rejected' in css
    assert '.kanban-bucket.raw' in css
    # Card elements
    assert '.card-title' in css or 'card-title' in css
    assert '.card-reason' in css or 'card-reason' in css
    # Scroll snap and smooth scrolling for carousel
    assert 'scroll-snap' in css
    assert 'scroll-behavior: smooth' in css
    # The nav arrows clear their outline, so they must draw their own focus ring.
    assert '.kanban-nav-btn:focus-visible' in css
    # Motion is opt-out.
    assert '@media (prefers-reduced-motion: reduce)' in css
    # `word-break: break-word` is deprecated; stylelint flags it.
    assert 'word-break: break-word' not in css
    assert 'overflow-wrap: anywhere' in css

    # Event ticker translation styles
    assert '.ticker-translation' in css
    assert '.ticker-raw' in css
    assert '.ticker-ctx' in css

    # Cancelled order toggle styles
    assert '.toggle-cancelled-btn' in css
    assert '.cancelled-count' in css
    assert '.order-cancelled' in css


# ── Reset feature ──

def test_html_has_reset_button():
    """The HTML has a reset button in the top nav."""
    html = _read_static("index.html")
    assert 'btn-reset' in html
    assert 'RESET' in html
    assert 'reset-modal' in html
    assert 'reset-input' in html
    assert 'reset-modal-confirm' in html
    assert 'reset-modal-close' in html
    assert 'reset-progress' in html


def test_app_js_has_reset_logic():
    """app.js has the reset modal logic and typed confirm."""
    app_js = _read_static("app.js")
    assert 'resetModal' in app_js
    assert 'resetConfirmBtn' in app_js
    assert "'RESET'" in app_js
    assert 'controlFetch' in app_js
    assert '/api/system/reset' in app_js
    # Escape closes reset modal
    assert 'resetModal' in app_js


def test_styles_css_has_reset_button():
    """CSS has styles for the reset button."""
    css = _read_static("styles.css")
    assert '.btn-reset' in css


def test_reset_endpoint_exists():
    """The /api/system/reset endpoint is registered on the app."""
    from dashboard.server import app
    routes = [r.path for r in app.routes if hasattr(r, 'path')]
    assert '/api/system/reset' in routes


def test_reset_clears_screener_files(client, temp_db, tmp_path, monkeypatch):
    """POST /api/system/reset clears markets.json, pipeline.json, and rerank.log."""
    import dashboard.server as dash_mod
    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    m_file = runtime_dir / "markets.json"
    p_file = runtime_dir / "pipeline.json"
    r_file = runtime_dir / "rerank.log"
    m_file.write_text("[]", encoding="utf-8")
    p_file.write_text("{}", encoding="utf-8")
    r_file.write_text("log", encoding="utf-8")

    res = client.post("/api/system/reset", headers=_control(client))
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") is True
    assert not m_file.exists()
    assert not p_file.exists()
    assert not r_file.exists()


def test_app_js_translates_a_failed_market_scan():
    """A failed scan leaves the Trader on a stale universe -- say so in words.

    scripts/filter_loop.py emits `filter|rerank_error`. Without a translation
    the ticker shows only the raw JSON line, which is the one event an
    operator most needs to read at a glance.
    """
    app_js = (Path(__file__).resolve().parent.parent
              / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")

    assert "'filter|rerank_error'" in app_js
    # The pre-rename tag is still accepted while an old ring is being read.
    assert "'screener|rerank_error'" in app_js


def test_only_the_decide_toggle_asks_for_typed_confirmation():
    """Each toggle drives its own service, and only Decide rests maker bids.

    Filter scans and Query reconciles -- neither opens risk -- so only the
    Decide toggle asks for the typed START confirmation.
    """
    app_js = (Path(__file__).resolve().parent.parent
              / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")

    toggle_block = app_js.split("querySelectorAll('.toggle[data-svc]')")[1]
    toggle_block = toggle_block.split("t.addEventListener('keydown'")[0]

    assert "/api/system/service/start" in toggle_block
    assert "/api/system/service/stop" in toggle_block
    assert "'/api/system/start'" not in toggle_block, (
        "a card toggle must never fire the whole-stack start"
    )
    assert "prompt(" in toggle_block, "the decide path must ask for confirmation"
    assert "confirmed !== 'START'" in toggle_block
    assert "svc === 'decide'" in toggle_block, (
        "the confirmation must sit on the decide card alone"
    )


# ── Active-database identity: LIVE vs SHADOW ────────────────────────────────


def test_system_status_reports_shadow_mode_for_non_production_db(client):
    """The status payload names the database it reads, and its mode.

    The dashboard renders identically against `data/orders.db` and against a
    shadow store, so an operator watching a shadow rehearsal has no way to tell
    which registry the numbers came from. The payload must say.
    """
    data = client.get("/api/system/status").json()

    assert data["db_mode"] == "SHADOW"
    assert data["db_is_production"] is False
    assert data["db_path"].endswith("live.db")


def test_system_status_reports_live_mode_for_production_registry(monkeypatch):
    """No override and no LIVE_DB_PATH resolves to the production registry."""
    import dashboard.server as dash_mod
    from core_brain.order_registry import DEFAULT_DB_PATH

    monkeypatch.delenv("LIVE_DB_PATH", raising=False)
    dash_mod.set_db_override(None)

    data = dash_mod.get_system_status()

    assert data["db_mode"] == "LIVE"
    assert data["db_is_production"] is True
    assert Path(data["db_path"]) == DEFAULT_DB_PATH


def test_db_identity_is_unknown_when_path_cannot_be_resolved(monkeypatch):
    """A path that will not resolve is never reported as the live registry.

    Fail closed: START is gated on `db_is_production`, so a path we cannot
    resolve must not carry the flag that unlocks a live stack.
    """
    import dashboard.server as dash_mod

    def _boom(self, *a, **k):
        raise OSError("unresolvable")

    monkeypatch.setattr(Path, "resolve", _boom)

    identity = dash_mod.resolve_db_identity(Path("data/whatever.db"))

    assert identity["mode"] == "UNKNOWN"
    assert identity["is_production"] is False


def test_start_bot_refuses_when_active_db_is_not_production(monkeypatch, tmp_path):
    """START is a live-money path; it refuses while the page reads a shadow store.

    start_bot always launches the stack against `data/orders.db`, whatever the
    dashboard is reading. Allowing it from a shadow view would run real orders
    behind a page that cannot show them.
    """
    import subprocess

    import dashboard.server as dash_mod

    spawned = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            spawned.append(args)
            self.pid = 12345

    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    monkeypatch.setattr(dash_mod, "resolve_sweep_interval", lambda: None)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    dash_mod.set_db_override(tmp_path / "shadow.db")
    try:
        result = dash_mod.start_bot()
    finally:
        dash_mod.set_db_override(None)

    assert result["ok"] is False
    assert "shadow.db" in result["message"]
    assert spawned == [], "no process may be launched from a shadow view"


def test_start_bot_refusal_leaves_no_start_lock(monkeypatch, tmp_path):
    """The refusal happens before the start lock, so a later live start works."""
    import subprocess

    import dashboard.server as dash_mod

    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    monkeypatch.setattr(dash_mod, "resolve_sweep_interval", lambda: None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: None)
    dash_mod.set_db_override(tmp_path / "shadow.db")
    try:
        dash_mod.start_bot()
    finally:
        dash_mod.set_db_override(None)

    assert not (tmp_path / "runtime" / ".bot_start.lock").exists()


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_shadow_view_toggle_never_prompts_or_posts_a_start():
    """The click itself is tested, not the words in the file.

    The server refuses a shadow-view START, but the page must not offer it
    either: a prompt that reads "this rests REAL maker bids" on a view that
    cannot show them teaches the operator the wrong thing about what the page
    is. Driven through the real handler in `app.js`, with the DOM stubbed --
    the same click on a LIVE view still prompts and still POSTs, which is what
    makes this fail if the guard is removed.
    """
    harness = Path(__file__).resolve().parent / "js" / "start_toggle_harness.cjs"
    app_js = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "app.js"

    res = subprocess.run(
        [NODE, str(harness), str(app_js)],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)

    # Shadow view: refused on the page, before any control request.
    assert out["shadow"]["prompts"] == 0
    assert out["shadow"]["starts"] == 0
    assert out["shadow"]["alerts"] == 1
    assert "production registry" in out["shadow"]["alertMsg"]
    assert "SHADOW" in out["badgeText"] and "shadow.db" in out["badgeText"]
    assert "shadow" in out["badgeClass"]

    # Live view, Decide card: prompts once and POSTs to its own endpoint,
    # never the whole-stack start.
    assert out["live"]["prompts"] == 1
    assert out["live"]["starts"] == 1
    assert out["live"]["wholeStackStarts"] == 0

    # Live view, Market Filter card: starts with no typed prompt.
    assert out["liveFilter"]["prompts"] == 0
    assert out["liveFilter"]["starts"] == 1
    assert out["liveFilter"]["wholeStackStarts"] == 0


def test_page_surfaces_the_active_database_mode():
    """The badge and its wiring ship with the page, not just in the payload."""
    assert "db-mode-badge" in _read_static("index.html")
    app_js = _read_static("app.js")
    assert "db_mode" in app_js
    assert "db_is_production" in app_js


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_event_ticker_formats_timestamps_in_local_time():
    """Event ticker renders timestamps in viewer's local time, while storage stays UTC."""
    harness = Path(__file__).resolve().parent / "js" / "format_local_time_harness.cjs"
    app_js = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "app.js"

    # Run in UTC+3 (Etc/GMT-3 in POSIX format)
    env = dict(os.environ)
    env["TZ"] = "Etc/GMT-3"

    res = subprocess.run(
        [NODE, str(harness), str(app_js)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)

    # Local time matches expected conversion in UTC+3 and differs from raw UTC
    assert out["valid"] is not None
    assert out["valid"] == out["expected"]
    assert "14:28:00" not in out["valid"]
    assert out["empty"] == ""
    assert out["invalid"] == ""
    assert out["nullVal"] == ""

    # SSE Event Ticker integration renders the local-formatted timestamp
    assert out["tickerHtml"] is not None
    assert out["expected"] in out["tickerHtml"]
    assert "14:28:00" not in out["tickerHtml"]

    # Live Event Ticker banner indicates local time
    index_html = _read_static("index.html")
    # The note was reworded to "(Local timestamps)" in the ticker header.
    # What matters is that the page SAYS the times are local, not the
    # exact sentence it says it in.
    lowered = index_html.lower()
    assert "local timestamps" in lowered or "local time" in lowered


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_market_link_generates_safe_hyperlinks():
    """marketLink generates safe anchor tags for markets with slug/url, escaping XSS."""
    harness = Path(__file__).resolve().parent / "js" / "market_link_harness.cjs"
    app_js = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "app.js"

    res = subprocess.run(
        [NODE, str(harness), str(app_js)],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)

    # With slug: generates anchor pointing to Polymarket
    assert '<a href="https://polymarket.com/market/btc-up"' in out["withSlug"]
    assert 'class="market-link"' in out["withSlug"]
    assert 'target="_blank"' in out["withSlug"]
    assert 'rel="noopener noreferrer"' in out["withSlug"]
    assert "BTC UP" in out["withSlug"]

    # With direct URL
    assert '<a href="https://polymarket.com/market/eth-down"' in out["withUrl"]
    assert 'class="market-link"' in out["withUrl"]
    assert 'target="_blank"' in out["withUrl"]
    assert 'rel="noopener noreferrer"' in out["withUrl"]
    assert "ETH DOWN" in out["withUrl"]

    # Without slug or URL: plain text, no anchor
    assert "<a" not in out["noUrlOrSlug"]
    assert "Plain Market" in out["noUrlOrSlug"]

    # Null value fallback
    assert out["nullVal"] == "--"

    # XSS escaping
    assert "<script>" not in out["xss"]
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in out["xss"]
    assert 'onclick="alert(1)' not in out["xss"]

    # CSS class present in styles.css
    styles_css = _read_static("styles.css")
    assert ".market-link" in styles_css


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_market_table_headers_and_cells_alignment():
    """Retired with the Data & Markets inspection table.

    The table this test parsed (#market-table, 6 aligned cells rendered by
    renderMarkets) is gone from Data & Markets -- the kanban shows the
    pipeline and the dashboard's Orders & Trades table shows the orders.
    The surviving tables keep their own shape tests (orders_trades_harness).
    """
    index_html = _read_static("index.html")
    import re
    assert re.search(r'<table id="market-table">', index_html) is None
    assert '<tbody id="market-body">' not in index_html
    assert "function renderMarkets(" not in _read_static("app.js")
    # The orders table the dashboard page still renders keeps its harness.
    assert (Path(__file__).resolve().parent / "js" / "orders_trades_harness.cjs").exists()



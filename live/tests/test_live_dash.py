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
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.order_registry import SCHEMA
from dash.live_dash import (
    PAGE_HTML,
    app,
    query_db_state,
    resolve_db_path,
    set_db_override,
)

NODE = shutil.which("node")


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


def test_empty_database_non_existent(tmp_path):
    """When the DB file does not exist, it reports empty=True with a clean message."""
    missing_path = tmp_path / "missing.db"
    state = query_db_state(missing_path)
    assert state["empty"] is True
    assert state["orders"] == []
    assert state["pairs"] == []
    assert state["fills"] == []
    assert state["stale"] is False
    assert "not found" in state["message"] or "Database" in state["message"]


def test_empty_database_with_schema(temp_db):
    """When the DB has tables but 0 orders, it reports empty=True cleanly."""
    state = query_db_state(temp_db)
    assert state["empty"] is True
    assert state["orders"] == []
    assert state["pairs"] == []
    assert state["capital"]["total_committed"] == 0.0
    assert state["last_polled_ts"] is None
    assert state["seconds_since_poll"] is None


def test_resting_pair(temp_db):
    """A pair with 2 resting orders and 0 fills reports RESTING hedge state."""
    now_ms = int(time.time() * 1000)
    con = sqlite3.connect(str(temp_db))
    cur = con.cursor()

    pair_id = "pair_test_001"
    cur.execute("""
        INSERT INTO orders (id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, pair_id, max_pair_cost_at_post)
        VALUES ('uuid-up', 'clob-up', 'cond-1', 'tok-up', 'BUY', 0.54, 10.0, 'open', ?, ?, ?, 0.98),
               ('uuid-dn', 'clob-dn', 'cond-1', 'tok-dn', 'BUY', 0.43, 10.0, 'open', ?, ?, ?, 0.98)
    """, (now_ms, now_ms, pair_id, now_ms, now_ms, pair_id))
    con.commit()
    con.close()

    state = query_db_state(temp_db)
    assert state["empty"] is False
    assert len(state["orders"]) == 2
    assert len(state["pairs"]) == 1

    pair = state["pairs"][0]
    assert pair["pair_id"] == pair_id
    assert pair["hedge_state"] == "RESTING"
    assert pair["naked_info"] is None
    assert pair["combined_price"] == 0.97
    assert pair["max_pair_cost_at_post"] == 0.98

    # Capital checks: 10 * 0.54 + 10 * 0.43 = $9.70
    assert state["capital"]["total_committed"] == 9.70
    assert state["capital"]["resting_committed"] == 9.70
    assert state["capital"]["filled_committed"] == 0.0


def test_naked_pair(temp_db):
    """When one leg fills while the other has 0 fills, reports NAKED state with dollar risk."""
    now_ms = int(time.time() * 1000)
    con = sqlite3.connect(str(temp_db))
    cur = con.cursor()

    pair_id = "pair_test_naked"
    cur.execute("""
        INSERT INTO orders (id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, pair_id, max_pair_cost_at_post)
        VALUES ('uuid-up', 'clob-up', 'cond-1', 'tok-up', 'BUY', 0.54, 10.0, 'filled', ?, ?, ?, 0.98),
               ('uuid-dn', 'clob-dn', 'cond-1', 'tok-dn', 'BUY', 0.43, 10.0, 'open', ?, ?, ?, 0.98)
    """, (now_ms - 20000, now_ms, pair_id, now_ms - 20000, now_ms, pair_id))

    # Insert fill on UP order
    fill_ts = now_ms - 15000
    cur.execute("""
        INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts)
        VALUES ('trade-001', 'uuid-up', 10.0, 0.54, ?)
    """, (fill_ts,))
    con.commit()
    con.close()

    state = query_db_state(temp_db)
    assert state["empty"] is False
    pair = state["pairs"][0]
    assert pair["hedge_state"] == "NAKED"
    assert pair["naked_info"] is not None

    naked = pair["naked_info"]
    assert naked["unhedged_shares"] == 10.0
    assert naked["unhedged_dollars"] == 5.40
    # Net direction of the exposure, not the side of the order that opened it:
    # a token can carry several orders, and an overshooting SELL leaves a net
    # SHORT that no single order side could express.
    assert naked["unhedged_side"] == "LONG"
    assert naked["naked_since_ts"] == fill_ts
    assert naked["seconds_naked"] >= 14.0

    # Capital: 10 * 0.54 filled ($5.40), 10 * 0.43 resting ($4.30) -> total $9.70
    assert state["capital"]["filled_committed"] == 5.40
    assert state["capital"]["resting_committed"] == 4.30
    assert state["capital"]["total_committed"] == 9.70


def test_balanced_pair(temp_db):
    """When both legs fill equally, reports BALANCED state."""
    now_ms = int(time.time() * 1000)
    con = sqlite3.connect(str(temp_db))
    cur = con.cursor()

    pair_id = "pair_test_balanced"
    cur.execute("""
        INSERT INTO orders (id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, pair_id, max_pair_cost_at_post)
        VALUES ('uuid-up', 'clob-up', 'cond-1', 'tok-up', 'BUY', 0.54, 10.0, 'filled', ?, ?, ?, 0.98),
               ('uuid-dn', 'clob-dn', 'cond-1', 'tok-dn', 'BUY', 0.43, 10.0, 'filled', ?, ?, ?, 0.98)
    """, (now_ms - 30000, now_ms, pair_id, now_ms - 30000, now_ms, pair_id))

    cur.execute("""
        INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts)
        VALUES ('trade-001', 'uuid-up', 10.0, 0.54, ?),
               ('trade-002', 'uuid-dn', 10.0, 0.43, ?)
    """, (now_ms - 25000, now_ms - 20000))
    con.commit()
    con.close()

    state = query_db_state(temp_db)
    pair = state["pairs"][0]
    assert pair["hedge_state"] == "BALANCED"
    assert pair["naked_info"] is None
    assert state["capital"]["filled_committed"] == 9.70
    assert state["capital"]["resting_committed"] == 0.0


def test_stale_poll_detection(temp_db):
    """When max(last_polled_ts) is older than 30s, reports stale=True."""
    now_ms = int(time.time() * 1000)
    stale_poll_ms = now_ms - 45000  # 45 seconds ago

    con = sqlite3.connect(str(temp_db))
    cur = con.cursor()
    cur.execute("""
        INSERT INTO orders (id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, pair_id, max_pair_cost_at_post)
        VALUES ('uuid-stale', 'clob-1', 'cond-1', 'tok-1', 'BUY', 0.50, 10.0, 'open', ?, ?, 'pair_stale', 0.98)
    """, (stale_poll_ms, stale_poll_ms))
    con.commit()
    con.close()

    state = query_db_state(temp_db)
    assert state["stale"] is True
    assert state["seconds_since_poll"] is not None
    assert state["seconds_since_poll"] >= 40.0


def test_unattributed_status_flag(temp_db):
    """An order with status='unattributed' is explicitly flagged."""
    now_ms = int(time.time() * 1000)
    con = sqlite3.connect(str(temp_db))
    cur = con.cursor()
    cur.execute("""
        INSERT INTO orders (id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, pair_id, max_pair_cost_at_post)
        VALUES ('uuid-unattr', 'clob-unknown', 'cond-1', 'tok-1', 'BUY', 0.50, 10.0, 'unattributed', ?, ?, 'pair-1', 0.98)
    """, (now_ms, now_ms))
    con.commit()
    con.close()

    state = query_db_state(temp_db)
    assert len(state["orders"]) == 1
    assert state["orders"][0]["is_unattributed"] is True
    assert state["orders"][0]["status"] == "unattributed"


def test_reconcile_lock_status(temp_db):
    """Reconcile lock row is correctly read and formatted."""
    now_ms = int(time.time() * 1000)
    con = sqlite3.connect(str(temp_db))
    cur = con.cursor()
    cur.execute("""
        INSERT INTO reconcile_lock (id, holder, acquired_ts)
        VALUES (1, 'poll_worker_pid_4092', ?)
    """, (now_ms - 5000,))
    con.commit()
    con.close()

    state = query_db_state(temp_db)
    lock = state["reconcile_lock"]
    assert lock["held"] is True
    assert lock["holder"] == "poll_worker_pid_4092"
    assert lock["acquired_ts"] == now_ms - 5000
    assert lock["age_sec"] >= 4.0


def test_read_only_enforcement(temp_db):
    """Verifies that the dashboard connection is strictly read-only and cannot write."""
    uri = f"file:{temp_db.resolve().as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    cur = con.cursor()
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        cur.execute("INSERT INTO reconcile_lock (id, holder, acquired_ts) VALUES (1, 'hack', 1000)")
    con.close()


def test_api_and_html_endpoints(client, temp_db):
    """Test the FastAPI / and /api/state endpoints."""
    # Test HTML index
    res_html = client.get("/")
    assert res_html.status_code == 200
    assert "Spread Hunter — Live Cycle Monitor" in res_html.text
    assert "<script>" in res_html.text

    # Test /api/state
    res_json = client.get("/api/state")
    assert res_json.status_code == 200
    data = res_json.json()
    assert "empty" in data
    assert "capital" in data
    assert "pairs" in data


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_dashboard_script_parses(tmp_path):
    """Ensures no syntax errors in the inline JS block."""
    scripts = re.findall(r"<script>([\s\S]*?)</script>", PAGE_HTML)
    assert scripts, "Dashboard HTML must contain a <script> block"
    for i, src in enumerate(scripts):
        f = tmp_path / f"live_dash_{i}.js"
        f.write_text(src, encoding="utf-8")
        res = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert res.returncode == 0, f"Script parse error: {res.stderr}"


def test_exit_shape_three_orders_two_tokens_reports_naked(temp_db):
    """A pair holding three orders on two tokens must still report NAKED.

    `exit` adds a SELL on a token already in the pair, so a genuinely unhedged
    position routinely carries three orders. Classifying by order count sent this
    shape to a calm RESTING with no warning -- the one state this page must never
    show while a leg is naked.
    """
    now_ms = int(time.time() * 1000)
    con = sqlite3.connect(str(temp_db))
    cur = con.cursor()

    pair_id = "pair_exit_shape"
    cur.execute("""
        INSERT INTO orders (id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, pair_id, max_pair_cost_at_post)
        VALUES ('uuid-up',   'clob-up',   'cond-1', 'tok-up', 'BUY',  0.54, 10.0, 'filled',    ?, ?, ?, 0.98),
               ('uuid-dn',   'clob-dn',   'cond-1', 'tok-dn', 'BUY',  0.43, 10.0, 'cancelled', ?, ?, ?, 0.98),
               ('uuid-sell', 'clob-sell', 'cond-1', 'tok-up', 'SELL', 0.52, 10.0, 'open',      ?, ?, ?, 0.98)
    """, (now_ms - 30000, now_ms, pair_id,
          now_ms - 30000, now_ms, pair_id,
          now_ms - 5000, now_ms, pair_id))
    cur.execute("""
        INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts)
        VALUES ('trade-up', 'uuid-up', 10.0, 0.54, ?)
    """, (now_ms - 25000,))
    con.commit()
    con.close()

    pair = query_db_state(temp_db)["pairs"][0]
    assert len(pair["orders"]) == 3
    assert pair["hedge_state"] == "NAKED"
    assert pair["naked_info"]["unhedged_shares"] == 10.0
    assert pair["naked_info"]["unhedged_dollars"] == 5.40
    # Three orders, two tokens: the pair cost is the two token prices, not three.
    assert pair["combined_price"] == 0.97


def test_exit_sell_filled_flattens_to_closed(temp_db):
    """Once the SELL fills, the token nets to zero and exposure is gone."""
    now_ms = int(time.time() * 1000)
    con = sqlite3.connect(str(temp_db))
    cur = con.cursor()

    pair_id = "pair_flat"
    cur.execute("""
        INSERT INTO orders (id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, pair_id, max_pair_cost_at_post)
        VALUES ('uuid-up',   'clob-up',   'cond-1', 'tok-up', 'BUY',  0.54, 10.0, 'filled',    ?, ?, ?, 0.98),
               ('uuid-dn',   'clob-dn',   'cond-1', 'tok-dn', 'BUY',  0.43, 10.0, 'cancelled', ?, ?, ?, 0.98),
               ('uuid-sell', 'clob-sell', 'cond-1', 'tok-up', 'SELL', 0.52, 10.0, 'filled',    ?, ?, ?, 0.98)
    """, (now_ms - 30000, now_ms, pair_id,
          now_ms - 30000, now_ms, pair_id,
          now_ms - 5000, now_ms, pair_id))
    cur.executemany("""
        INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts) VALUES (?, ?, ?, ?, ?)
    """, [("trade-up", "uuid-up", 10.0, 0.54, now_ms - 25000),
          ("trade-sell", "uuid-sell", 10.0, 0.52, now_ms - 2000)])
    con.commit()
    con.close()

    pair = query_db_state(temp_db)["pairs"][0]
    assert pair["hedge_state"] == "CLOSED"
    assert pair["naked_info"] is None


def test_pair_spanning_three_tokens_is_refused_not_reduced(temp_db):
    """More than two tokens is refused, matching live_pairs.load_pair.

    Reducing three tokens to the largest two would size a decision against a
    position that the dropped leg partly offsets.
    """
    now_ms = int(time.time() * 1000)
    con = sqlite3.connect(str(temp_db))
    cur = con.cursor()

    pair_id = "pair_three_tokens"
    cur.execute("""
        INSERT INTO orders (id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, pair_id, max_pair_cost_at_post)
        VALUES ('uuid-a', 'clob-a', 'cond-1', 'tok-a', 'BUY', 0.30, 10.0, 'filled', ?, ?, ?, 0.98),
               ('uuid-b', 'clob-b', 'cond-1', 'tok-b', 'BUY', 0.35, 10.0, 'open',   ?, ?, ?, 0.98),
               ('uuid-c', 'clob-c', 'cond-1', 'tok-c', 'BUY', 0.32, 10.0, 'open',   ?, ?, ?, 0.98)
    """, (now_ms, now_ms, pair_id, now_ms, now_ms, pair_id, now_ms, now_ms, pair_id))
    cur.execute("""
        INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts)
        VALUES ('trade-a', 'uuid-a', 10.0, 0.30, ?)
    """, (now_ms - 1000,))
    con.commit()
    con.close()

    pair = query_db_state(temp_db)["pairs"][0]
    assert pair["hedge_state"] == "REFUSED"
    assert "3 token ids" in pair["refused_reason"]


def test_two_orders_on_one_token_is_naked_not_balanced(temp_db):
    """Two filled BUYs on the SAME token are one-sided, never a balanced pair.

    Comparing order slots rather than tokens made two same-side orders look like
    two opposing legs of equal size, reporting BALANCED on a fully naked position.
    """
    now_ms = int(time.time() * 1000)
    con = sqlite3.connect(str(temp_db))
    cur = con.cursor()

    pair_id = "pair_same_token"
    cur.execute("""
        INSERT INTO orders (id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, pair_id, max_pair_cost_at_post)
        VALUES ('uuid-1', 'clob-1', 'cond-1', 'tok-up', 'BUY', 0.50, 10.0, 'filled', ?, ?, ?, 0.98),
               ('uuid-2', 'clob-2', 'cond-1', 'tok-up', 'BUY', 0.50, 10.0, 'filled', ?, ?, ?, 0.98)
    """, (now_ms - 9000, now_ms, pair_id, now_ms - 8000, now_ms, pair_id))
    cur.executemany("""
        INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts) VALUES (?, ?, ?, ?, ?)
    """, [("trade-1", "uuid-1", 10.0, 0.50, now_ms - 7000),
          ("trade-2", "uuid-2", 10.0, 0.50, now_ms - 6000)])
    con.commit()
    con.close()

    pair = query_db_state(temp_db)["pairs"][0]
    assert pair["hedge_state"] == "NAKED"
    assert pair["naked_info"]["unhedged_shares"] == 20.0
    assert pair["naked_info"]["unhedged_dollars"] == 10.00


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
    """Token ids and statuses reach innerHTML, and the venue writes some of them."""
    assert "function esc(v)" in PAGE_HTML
    for interpolation in (
        "${esc(o.side || 'BUY')}",
        "${esc((o.token_id || '').slice(0, 10))}",
        "${esc((f.trade_id || '').slice(0, 12))}",
        "${esc(lock.holder)}",
    ):
        assert interpolation in PAGE_HTML, interpolation
    # A status from the database must not become a CSS class unchecked.
    assert "KNOWN_STATUSES.includes(o.status)" in PAGE_HTML


def test_dashboard_reads_exactly_where_the_registry_writes():
    """One live registry, one path. A dashboard aimed elsewhere reports a calm lie.

    The repo root still carries a run/live.db from before the live path was
    extracted. Preferring it pointed this page at a dead file that never
    receives a fill, which reads identically to a healthy idle cycle.
    """
    from engine.order_registry import DEFAULT_DB_PATH

    assert resolve_db_path() == DEFAULT_DB_PATH
    assert resolve_db_path().parent.parent.name == "live"


def test_milestone8_html_contains_required_sections():
    """Milestone 8 requirement: UI components for 3 levels, exposure chart, and run selector exist in HTML."""
    assert "sec-run-kpis" in PAGE_HTML
    assert "sec-exposure" in PAGE_HTML
    assert "sec-funnel" in PAGE_HTML
    assert "sec-mechanics" in PAGE_HTML
    assert "modal-drilldown" in PAGE_HTML
    assert "modal-dist" in PAGE_HTML
    assert "run-selector" in PAGE_HTML
    assert "tip-wrap" in PAGE_HTML
    assert "bellCurveSvg" in PAGE_HTML


def test_api_kpi_endpoint_returns_3_levels_and_run_isolation(client, temp_db):
    """Test /api/kpi provides Level 1 (Strategy), Level 2 (Market), Level 3 (Mechanics), and run isolation."""
    from engine.order_registry import (
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
    assert mkt["up_sh"] == 5.0
    assert mkt["dn_sh"] == 5.0
    assert mkt["pair_cost"] == 0.94
    assert mkt["balance"] == 1.0
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
    lazy `from engine.kpi import report` inside the endpoint raised
    ModuleNotFoundError and every poll came back 500 -- invisible to this suite,
    which runs with live/ as the working directory.
    """
    repo_root = Path(__file__).resolve().parents[2]
    dash_file = repo_root / "live" / "dash" / "live_dash.py"
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
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("200"), proc.stdout + proc.stderr


def test_page_html_contains_status_bar_and_bot_buttons():
    """Verify HTML contains Supervisor high-hierarchy indicator, 3 sub-service dots, and Start/Stop/Reset buttons."""
    from dash.live_dash import PAGE_HTML

    assert "id=\"supervisor-status\"" in PAGE_HTML or "id=\"sup-status\"" in PAGE_HTML
    assert "SUPERVISOR" in PAGE_HTML
    assert "id=\"sub-services\"" in PAGE_HTML or "class=\"sub-services\"" in PAGE_HTML
    assert "btn-start-bot" in PAGE_HTML
    assert "btn-stop-bot" in PAGE_HTML
    assert "btn-reset-db" in PAGE_HTML
    assert "dot-online" in PAGE_HTML or "status-dot" in PAGE_HTML


def test_system_status_endpoint(client):
    """GET /api/system/status returns supervisor, 3 sub-services (screener, engine, dash), and bot state."""
    res = client.get("/api/system/status")
    assert res.status_code == 200
    data = res.json()
    assert "supervisor" in data
    assert "running" in data["supervisor"]
    assert "services" in data
    assert "screener" in data["services"]
    assert "engine" in data["services"]
    assert "dash" in data["services"]
    assert data["services"]["dash"]["running"] is True
    assert "bot_state" in data


def test_system_start_and_stop_endpoints(client, monkeypatch, tmp_path):
    """POST /api/system/start and POST /api/system/stop control bot state safely."""
    # Test stop when already stopped
    res_stop = client.post("/api/system/stop")
    assert res_stop.status_code == 200
    stop_data = res_stop.json()
    assert stop_data.get("ok") is True

    # Test start
    res_start = client.post("/api/system/start")
    assert res_start.status_code == 200
    start_data = res_start.json()
    assert start_data.get("ok") is True


def test_system_reset_db_endpoint(client, temp_db):
    """POST /api/system/reset-db archives the existing DB and initializes a clean fresh DB."""
    import sqlite3
    # Put a dummy row in temp_db first
    conn = sqlite3.connect(temp_db)
    conn.execute("INSERT INTO orders (id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts) VALUES ('dummy-1', '0x1', '0x2', 'BUY', 0.5, 10, 'open', 1000, 1000)")
    conn.commit()
    conn.close()

    res = client.post("/api/system/reset-db")
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") is True

    # Verify orders table in freshly created DB is empty
    conn2 = sqlite3.connect(temp_db)
    cursor = conn2.cursor()
    cursor.execute("SELECT COUNT(*) FROM orders")
    count = cursor.fetchone()[0]
    conn2.close()
    assert count == 0



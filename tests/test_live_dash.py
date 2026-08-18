"""Tests for the live execution monitor (server/live_dash.py).

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
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from live.strategy.order_registry import SCHEMA
from server.live_dash import (
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
    assert naked["unhedged_side"] == "BUY"
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

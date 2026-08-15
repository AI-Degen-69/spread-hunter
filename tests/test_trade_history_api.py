"""Unit tests for /api/trade_history endpoint.

Ensures strict per-tier database isolation (no data mixing), market-level aggregation,
spread capture merge / sell classification, and order-level drilldown fidelity.
"""
from __future__ import annotations

import sys
import sqlite3
import pytest
from pathlib import Path

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from server.spread_dash import app


@pytest.fixture
def test_client(tmp_path: Path, monkeypatch):
    """Create isolated test bankroll databases for tiers 100, 200, and 500."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create bankroll_100 with a SPREAD CAPTURE MERGE trade
    tier100_dir = run_dir / "bankroll_100"
    tier100_dir.mkdir(parents=True, exist_ok=True)
    db100 = tier100_dir / "fleet.db"
    conn100 = sqlite3.connect(str(db100))
    conn100.execute("""
        CREATE TABLE closes (
            id INTEGER PRIMARY KEY, ts REAL, market_slug TEXT, condition_id TEXT,
            method TEXT, gas REAL, shares REAL, up_price REAL, dn_price REAL,
            cost_basis REAL, proceeds REAL, fee REAL, realized_pnl REAL,
            forgone_vs_settlement REAL
        )
    """)
    conn100.execute("""
        CREATE TABLE fills (
            id INTEGER PRIMARY KEY, ts REAL, market_slug TEXT, condition_id TEXT,
            token_id TEXT, side TEXT, size REAL, price REAL, fee REAL, order_id TEXT
        )
    """)
    conn100.execute("""
        CREATE TABLE quotes (
            id INTEGER PRIMARY KEY, ts REAL, market_slug TEXT, condition_id TEXT,
            token_id TEXT, side TEXT, price REAL, size REAL, status TEXT
        )
    """)
    conn100.execute("""
        CREATE TABLE live_state (
            market_slug TEXT PRIMARY KEY, condition_id TEXT, up_token TEXT, dn_token TEXT,
            up_shares REAL, dn_shares REAL, up_cost REAL, dn_cost REAL, state TEXT, last_seen REAL
        )
    """)
    conn100.execute("""
        CREATE TABLE resolutions (
            condition_id TEXT PRIMARY KEY, winning_token TEXT, resolved_ts REAL, payout REAL
        )
    """)

    # Populate tier 100: Market A merged at $1.00 with $0.48 YES + $0.49 NO = $0.97 cost basis (+$0.03 profit)
    conn100.execute(
        "INSERT INTO closes VALUES (1, 1000.0, 'will-btc-hit-100k', '0xcondA', 'merge', 0.001, 10.0, 0.48, 0.49, 9.70, 10.00, 0.0, 0.299, 0.0)"
    )
    conn100.execute(
        "INSERT INTO fills VALUES (1, 990.0, 'will-btc-hit-100k', '0xcondA', 'tok_yes', 'UP', 10.0, 0.48, 0.0, 'ord1')"
    )
    conn100.execute(
        "INSERT INTO fills VALUES (2, 995.0, 'will-btc-hit-100k', '0xcondA', 'tok_no', 'DN', 10.0, 0.49, 0.0, 'ord2')"
    )
    conn100.commit()
    conn100.close()

    # 2. Create bankroll_500 with an OPEN market with active resting bids
    tier500_dir = run_dir / "bankroll_500"
    tier500_dir.mkdir(parents=True, exist_ok=True)
    db500 = tier500_dir / "fleet.db"
    conn500 = sqlite3.connect(str(db500))
    conn500.execute("""
        CREATE TABLE closes (
            id INTEGER PRIMARY KEY, ts REAL, market_slug TEXT, condition_id TEXT,
            method TEXT, gas REAL, shares REAL, up_price REAL, dn_price REAL,
            cost_basis REAL, proceeds REAL, fee REAL, realized_pnl REAL,
            forgone_vs_settlement REAL
        )
    """)
    conn500.execute("""
        CREATE TABLE fills (
            id INTEGER PRIMARY KEY, ts REAL, market_slug TEXT, condition_id TEXT,
            token_id TEXT, side TEXT, size REAL, price REAL, fee REAL, order_id TEXT
        )
    """)
    conn500.execute("""
        CREATE TABLE quotes (
            id INTEGER PRIMARY KEY, ts REAL, market_slug TEXT, condition_id TEXT,
            token_id TEXT, side TEXT, price REAL, size REAL, status TEXT
        )
    """)
    conn500.execute("""
        CREATE TABLE live_state (
            market_slug TEXT PRIMARY KEY, condition_id TEXT, up_token TEXT, dn_token TEXT,
            up_shares REAL, dn_shares REAL, up_cost REAL, dn_cost REAL, state TEXT, last_seen REAL
        )
    """)
    conn500.execute("""
        CREATE TABLE resolutions (
            condition_id TEXT PRIMARY KEY, winning_token TEXT, resolved_ts REAL, payout REAL
        )
    """)

    # Populate tier 500: Market B with open resting quotes and partial fills
    conn500.execute(
        "INSERT INTO fills VALUES (1, 1020.0, 'will-eth-hit-5k', '0xcondB', 'tok_yes', 'UP', 5.0, 0.45, 0.0, 'ordB1')"
    )
    conn500.execute(
        "INSERT INTO quotes VALUES (1, 1025.0, 'will-eth-hit-5k', '0xcondB', 'tok_no', 'DN', 0.52, 5.0, 'RESTING')"
    )
    conn500.execute(
        "INSERT INTO live_state VALUES ('will-eth-hit-5k', '0xcondB', 'tok_yes', 'tok_no', 5.0, 0.0, 2.25, 0.0, 'QUOTING', 1030.0)"
    )
    conn500.commit()
    conn500.close()

    # Monkeypatch ROOT or run_dir in server.spread_dash
    from server import spread_dash
    monkeypatch.setattr(spread_dash, "BANKROLL_RUN_DIR", run_dir)

    return TestClient(app)


def test_api_trade_history_all(test_client):
    """Test /api/trade_history returns both tiers cleanly without contamination."""
    resp = test_client.get("/api/trade_history")
    assert resp.status_code == 200
    data = resp.json()
    assert "markets" in data
    assert len(data["markets"]) == 2

    # Check Tier 100 Market (Spread Capture Merge)
    m100 = next(m for m in data["markets"] if m["tier"] == 100)
    assert m100["market_slug"] == "will-btc-hit-100k"
    assert m100["status"] == "MERGED (SPREAD CAPTURE)"
    assert m100["method"] == "MERGE"
    assert m100["realized_pnl"] == pytest.approx(0.299, rel=1e-3)
    assert len(m100["orders"]) == 3  # 1 close event + 2 fills

    # Check Tier 500 Market (Open position with resting bid)
    m500 = next(m for m in data["markets"] if m["tier"] == 500)
    assert m500["market_slug"] == "will-eth-hit-5k"
    assert m500["status"] == "OPEN"
    assert m500["open_orders_count"] == 1
    assert len(m500["orders"]) == 2  # 1 fill + 1 quote


def test_api_trade_history_tier_filter(test_client):
    """Test ?tier=100 returns strictly tier 100 markets and zero tier 500 markets."""
    resp = test_client.get("/api/trade_history?tier=100")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["markets"]) == 1
    assert data["markets"][0]["tier"] == 100

    resp500 = test_client.get("/api/trade_history?tier=500")
    assert resp500.status_code == 200
    data500 = resp500.json()
    assert len(data500["markets"]) == 1
    assert data500["markets"][0]["tier"] == 500


def test_api_trade_history_production_schema(tmp_path: Path, monkeypatch):
    """Test /api/trade_history against true strategy/store.py schemas."""
    import json
    run_dir = tmp_path / "prod_run"
    tier200_dir = run_dir / "bankroll_200"
    tier200_dir.mkdir(parents=True, exist_ok=True)
    db = tier200_dir / "fleet.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE closes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, market_slug TEXT, condition_id TEXT,
            method TEXT, gas REAL, shares REAL, up_price REAL, dn_price REAL,
            cost_basis REAL, proceeds REAL, fee REAL, realized_pnl REAL, forgone_vs_settlement REAL
        )
    """)
    conn.execute("""
        CREATE TABLE fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, quote_id INTEGER, market_slug TEXT,
            condition_id TEXT, token_id TEXT, side TEXT, price REAL, size REAL,
            mid_at_post REAL, edge_vs_mid REAL, queue_waited REAL, seconds_to_fill REAL, crossed INTEGER, reason TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, market_slug TEXT, condition_id TEXT,
            token_id TEXT, side TEXT, price REAL, size REAL, queue_ahead REAL, mid REAL,
            edge_vs_mid REAL, t_remaining REAL, filled REAL DEFAULT 0, fill_ts REAL, cancelled INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE live_state (
            id INTEGER PRIMARY KEY CHECK (id = 1), ts REAL, payload TEXT
        )
    """)
    # Insert a closed market (MERGE) and an active quote
    conn.execute("INSERT INTO closes (ts, market_slug, condition_id, method, gas, shares, up_price, dn_price, cost_basis, proceeds, fee, realized_pnl) VALUES (100.0, 'will-btc-hit-100k', '0xbtc', 'MERGE', 0.0, 50.0, 0.48, 0.49, 48.5, 50.0, 0.0, 1.5)")
    conn.execute("INSERT INTO fills (ts, quote_id, market_slug, condition_id, token_id, side, price, size) VALUES (90.0, 1, 'will-btc-hit-100k', '0xbtc', 'tok1', 'UP', 0.48, 50.0)")
    conn.execute("INSERT INTO fills (ts, quote_id, market_slug, condition_id, token_id, side, price, size) VALUES (95.0, 2, 'will-btc-hit-100k', '0xbtc', 'tok2', 'DOWN', 0.49, 50.0)")
    conn.execute("INSERT INTO quotes (ts, market_slug, condition_id, token_id, side, price, size, filled, cancelled) VALUES (110.0, 'will-sol-hit-300', '0xsol', 'tok3', 'UP', 0.35, 20.0, 0.0, 0)")
    conn.execute("INSERT INTO live_state (id, ts, payload) VALUES (1, 115.0, ?)", (json.dumps({"slug": "will-sol-hit-300", "condition_id": "0xsol", "up_shares": 10.0, "dn_shares": 0.0}),))
    conn.commit()
    conn.close()

    from server import spread_dash
    monkeypatch.setattr(spread_dash, "BANKROLL_RUN_DIR", run_dir)
    client = TestClient(app)
    resp = client.get("/api/trade_history?tier=200")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["markets"]) == 2
    slugs = {m["market_slug"] for m in data["markets"]}
    assert "will-btc-hit-100k" in slugs
    assert "will-sol-hit-300" in slugs


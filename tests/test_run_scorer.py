"""Tests for core_brain.run_scorer and shadow run finish-line mechanism."""
from __future__ import annotations

import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core_brain.run_scorer import (
    ActionMetric,
    ActionThresholds,
    RunReliabilityScore,
    render_scoreboard,
    score_run,
    main as scorer_main,
)
from core_brain.shadow_run import _FinishLine


def _create_test_db(db_path: Path, *, orders=0, fills=0, closes_stop=0, closes_merge=0, run_id="test-run"):
    """Helper to populate an isolated SQLite DB with test actions."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            order_id TEXT,
            condition_id TEXT,
            token_id TEXT,
            side TEXT,
            price REAL,
            original_size REAL,
            status TEXT,
            posted_ts INTEGER,
            last_polled_ts INTEGER,
            pair_id TEXT,
            max_pair_cost_at_post REAL,
            cancel_reason TEXT,
            cancel_queue_ahead REAL,
            run_id TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fills (
            trade_id TEXT PRIMARY KEY,
            order_uuid TEXT,
            size REAL,
            price REAL,
            venue_ts INTEGER,
            recorded_ts INTEGER,
            run_id TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS closes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            condition_id TEXT,
            market_slug TEXT,
            method TEXT,
            gas REAL,
            shares REAL,
            up_price REAL,
            dn_price REAL,
            cost_basis REAL,
            proceeds REAL,
            fee REAL,
            realized_pnl REAL,
            forgone_vs_settlement REAL,
            up_cost_removed REAL,
            dn_cost_removed REAL,
            tx_hash TEXT,
            run_id TEXT,
            reason TEXT
        )
    """)

    for i in range(orders):
        cur.execute(
            "INSERT INTO orders (id, order_id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts, run_id) "
            "VALUES (?, ?, 'cid-test', 'tok-test', 'BUY', 0.5, 10.0, 'filled', 1000, 1000, ?)",
            (f"ord-{i}", f"oid-{i}", run_id),
        )

    for i in range(fills):
        cur.execute(
            "INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts, run_id) VALUES (?, ?, 10.0, 0.5, 1000, ?)",
            (f"fill-{i}", f"ord-{i}", run_id),
        )

    for i in range(closes_stop):
        cur.execute(
            "INSERT INTO closes (ts, method, reason, realized_pnl, run_id) VALUES (1000.0, 'single_buy_exit', 'lifecycle_hard_stop', -0.5, ?)",
            (run_id,),
        )

    for i in range(closes_merge):
        cur.execute(
            "INSERT INTO closes (ts, method, reason, realized_pnl, run_id) VALUES (1000.0, 'shadow_merge', NULL, 0.05, ?)",
            (run_id,),
        )

    conn.commit()
    conn.close()


def test_score_run_missing_db(tmp_path):
    missing = tmp_path / "does_not_exist.db"
    res = score_run(missing)
    assert res.authenticity_score_pct == 0.0
    assert res.confidence_tier == "NO_DATA"
    assert not res.is_reliable
    assert not res.finish_line_reached
    assert len(res.bottlenecks) == 4


def test_score_run_empty_db(tmp_path):
    db = tmp_path / "empty.db"
    _create_test_db(db, orders=0, fills=0, closes_stop=0, closes_merge=0)
    res = score_run(db)
    assert res.authenticity_score_pct == 0.0
    assert res.confidence_tier == "INSUFFICIENT"
    assert not res.is_reliable
    assert res.total_closed_trades == 0
    assert res.total_fills == 0
    assert set(res.bottlenecks) == {"orders", "positions_going", "stop_loss_exits", "positions_merged"}


def test_score_run_partial_actions(tmp_path):
    db = tmp_path / "partial.db"
    # Target defaults: orders=50, fills=20, stops=5, merges=15
    # Provide: 25 orders (50%), 10 fills (50%), 5 stops (100%), 0 merges (0%)
    _create_test_db(db, orders=25, fills=10, closes_stop=5, closes_merge=0)
    res = score_run(db)
    # Average of 0.5 + 0.5 + 1.0 + 0.0 = 2.0 / 4 = 0.50 -> 50.0%
    assert res.authenticity_score_pct == 50.0
    assert res.confidence_tier == "PROVISIONAL"
    assert not res.is_reliable
    assert "orders" in res.bottlenecks
    assert "positions_going" in res.bottlenecks
    assert "positions_merged" in res.bottlenecks
    assert "stop_loss_exits" not in res.bottlenecks
    assert res.actions["stop_loss_exits"].met is True
    assert res.actions["positions_merged"].met is False


def test_score_run_data_saturated_reliable(tmp_path):
    db = tmp_path / "saturated.db"
    # Target defaults: orders=50, fills=20, stops=5, merges=15
    _create_test_db(db, orders=60, fills=25, closes_stop=6, closes_merge=20)
    res = score_run(db)
    assert res.authenticity_score_pct == 100.0
    assert res.confidence_tier == "DATA_SATURATED"
    assert res.is_reliable is True
    assert res.finish_line_reached is True
    assert len(res.bottlenecks) == 0
    assert res.actions["orders"].met is True
    assert res.actions["positions_going"].met is True
    assert res.actions["stop_loss_exits"].met is True
    assert res.actions["positions_merged"].met is True


def test_score_run_with_target_trades_finish_line(tmp_path):
    db = tmp_path / "trades_target.db"
    # Total closed trades = 5 stops + 15 merges = 20
    _create_test_db(db, orders=50, fills=20, closes_stop=5, closes_merge=15)
    # Require 50 total trades
    thresh = ActionThresholds(orders=50, positions_going=20, stop_loss_exits=5, positions_merged=15, target_trades=50)
    res = score_run(db, thresholds=thresh)
    assert res.is_reliable is True  # all 4 action dimensions met
    assert res.finish_line_reached is False  # but trades target (20/50) not met
    assert any("target_trades" in b for b in res.bottlenecks)


def test_score_run_run_id_filtering(tmp_path):
    db = tmp_path / "multirun.db"
    _create_test_db(db, orders=50, fills=20, closes_stop=5, closes_merge=15, run_id="run-alpha")
    # Query for run-alpha -> saturated
    res_alpha = score_run(db, run_id="run-alpha")
    assert res_alpha.is_reliable is True
    assert res_alpha.actions["orders"].observed == 50

    # Query for run-beta -> empty
    res_beta = score_run(db, run_id="run-beta")
    assert res_beta.is_reliable is False
    assert res_beta.actions["orders"].observed == 0


def test_render_scoreboard(tmp_path):
    db = tmp_path / "display.db"
    _create_test_db(db, orders=50, fills=20, closes_stop=5, closes_merge=15)
    res = score_run(db)
    text = render_scoreboard(res)
    assert "RUN AUTHENTICITY & RELIABILITY SCOREBOARD" in text
    assert "Orders Placed" in text
    assert "Positions Going (Fills)" in text
    assert "Stop Loss Exits" in text
    assert "Positions Merged" in text
    assert "RELIABLE" in text


def test_run_scorer_cli(tmp_path, capsys):
    db = tmp_path / "cli.db"
    _create_test_db(db, orders=50, fills=20, closes_stop=5, closes_merge=15)

    # Test text mode
    rc = scorer_main(["--db", str(db)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "RUN AUTHENTICITY & RELIABILITY SCOREBOARD" in captured.out

    # Test json mode
    rc_json = scorer_main(["--db", str(db), "--json"])
    assert rc_json == 0
    captured_json = capsys.readouterr()
    parsed = json.loads(captured_json.out)
    assert parsed["authenticity_score_pct"] == 100.0
    assert parsed["is_reliable"] is True


def test_finish_line_exception():
    assert issubclass(_FinishLine, KeyboardInterrupt)


def test_dashboard_reliability_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    from dashboard.server import app

    db = tmp_path / "dash_test.db"
    _create_test_db(db, orders=50, fills=20, closes_stop=5, closes_merge=15)

    client = TestClient(app)
    res = client.get(f"/api/reliability?db={db}")
    assert res.status_code == 200
    data = res.json()
    assert data["authenticity_score_pct"] == 100.0
    assert data["is_reliable"] is True
    assert data["confidence_tier"] == "DATA_SATURATED"
    assert data["actions"]["orders"]["observed"] == 50
    assert data["actions"]["stop_loss_exits"]["observed"] == 5
    assert data["actions"]["positions_merged"]["observed"] == 15


def test_shadow_run_finish_line_triggers(tmp_path):
    from core_brain.shadow_run import run_shadow
    from core_brain.order_registry import OrderRegistry

    db = tmp_path / "shadow_finish.db"
    OrderRegistry(db)
    _create_test_db(db, orders=50, fills=20, closes_stop=5, closes_merge=15)

    # run_shadow with finish_line_reliable=True should immediately hit the finish line on rotation
    called_cycles = []

    def mock_markets(_=None):
        return []

    res = run_shadow(
        minutes=1.0,
        db_path=db,
        run_id="test-run",
        markets_fn=mock_markets,
        sleep_fn=lambda s: None,
        finish_line_reliable=True,
    )
    assert res is not None


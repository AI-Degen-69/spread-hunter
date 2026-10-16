"""Cockpit order-lifecycle funnel (#492 cockpit rework).

Covers three operator-facing behaviours:
1. The Issue / Port / Fills columns are gone from the leaderboard table.
2. Stop-Loss Exits counts closes by stop-loss method
   (single_buy_exit / naked_exit / ladder_exit), not "n - merges".
3. The funnel counts Quoted (quote rows) -> Filled (distinct orders with
   fills, legs) -> Merged (each pair counts 2 legs) -> Stop-Loss.
"""
from __future__ import annotations

import contextlib
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tournament_cockpit_server import (  # noqa: E402
    analyze_arm_database,
    generate_cockpit_html,
)

RUN_ID = "shadow-test-funnel"

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    run_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS queue_marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    run_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fills (
    trade_id TEXT PRIMARY KEY,
    order_uuid TEXT NOT NULL,
    size REAL NOT NULL,
    price REAL NOT NULL,
    venue_ts INTEGER,
    recorded_ts INTEGER,
    run_id TEXT,
    FOREIGN KEY (order_uuid) REFERENCES orders(id)
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    original_size REAL NOT NULL,
    status TEXT NOT NULL,
    posted_ts INTEGER NOT NULL,
    last_polled_ts INTEGER NOT NULL,
    pair_id TEXT,
    run_id TEXT
);
CREATE TABLE IF NOT EXISTS closes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    condition_id TEXT,
    market_slug TEXT,
    method TEXT DEFAULT 'sell',
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
    run_id TEXT NOT NULL,
    trigger_bid REAL,
    fresh_bid REAL
);
"""


def _seed(tmp_path: Path) -> Path:
    db = tmp_path / "funnel.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    # 6 quoted quote-rows
    conn.execute(
        "INSERT INTO quotes (ts, run_id) VALUES " +
        ",".join(f"({1000.0 + i}, '{RUN_ID}')" for i in range(6))
    )
    # 4 filled singular legs (5 fill events across 4 distinct orders)
    for i, oid in enumerate(("ord-up-1", "ord-dn-1", "ord-up-2", "ord-dn-2")):
        conn.execute(
            "INSERT INTO fills (trade_id, order_uuid, size, price, run_id) "
            f"VALUES ('t{i}', '{oid}', 5.0, 0.48, '{RUN_ID}')"
        )
    conn.execute(
        "INSERT INTO fills (trade_id, order_uuid, size, price, run_id) "
        f"VALUES ('t9', 'ord-up-1', 1.0, 0.48, '{RUN_ID}')"
    )
    # 1 merge pair (counts 2 legs) + 1 stop-loss exit + 1 other tracked close
    conn.execute(
        "INSERT INTO closes (ts, method, realized_pnl, run_id) "
        f"VALUES (1001.0, 'shadow_merge', 0.04, '{RUN_ID}')"
    )
    conn.execute(
        "INSERT INTO closes (ts, method, realized_pnl, run_id) "
        f"VALUES (1002.0, 'single_buy_exit', -0.03, '{RUN_ID}')"
    )
    # naked_exit too -- the whole stop-loss family counts
    conn.execute(
        "INSERT INTO closes (ts, method, realized_pnl, run_id) "
        f"VALUES (1003.0, 'naked_exit', -0.02, '{RUN_ID}')"
    )
    # settlement rows are excluded from every funnel count
    conn.execute(
        "INSERT INTO closes (ts, method, realized_pnl, run_id) "
        f"VALUES (1004.0, 'settlement', 0.10, '{RUN_ID}')"
    )
    conn.commit()
    conn.close()
    return db


def test_cockpit_funnel_counts(tmp_path):
    db = _seed(tmp_path)
    arm = analyze_arm_database({
        "issue": 492,
        "name": "funnel-arm",
        "label": "Funnel Arm",
        "port": 8999,
        "db": str(db),
        "run_id": RUN_ID,
    })
    funnel = arm["funnel"]
    assert funnel["quoted"] == 6
    # distinct orders with fills = filled legs (singular), not fill events
    assert funnel["filled"] == 4
    # the one merge pair counts as 2 legs
    assert funnel["merged"] == 1
    assert funnel["merged_legs"] == 2
    # stop-loss family: single_buy_exit + naked_exit
    assert funnel["stop_loss"] == 2
    assert arm["stop_loss_exits_count"] == 2


def test_cockpit_table_drops_issue_port_fills_columns():
    html = generate_cockpit_html({"arms": [], "tourney_key": "492"})
    for gone in ("<th>Issue</th>", "<th>Port</th>", "<th>Fills</th>"):
        assert gone not in html, f"{gone} column should be removed"
    # #608 KPI swap repinned this list: Quoted Orders (volume, no quality
    # signal) and Merged-legs (Merge % already carries the ratio) are gone;
    # the already-computed Sharpe / Profit Factor / VaR95 / MC P5 are promoted.
    for gone in ("<th>Quoted Orders</th>", "<th>Merged (legs \u00d72)</th>"):
        assert gone not in html, f"{gone} column should be removed"
    for kept in (
        "<th>Sharpe</th>",
        "<th>Profit Factor</th>",
        "<th>VaR95</th>",
        "<th>MC P5</th>",
        "<th>Filled Orders</th>",
        "<th>Stop-Loss Exits</th>",
    ):
        assert kept in html, f"{kept} funnel column should be present"


def test_cockpit_no_trades_returns_zero_funnel(tmp_path):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    arm = analyze_arm_database({
        "issue": 492,
        "name": "empty-arm",
        "label": "Empty Arm",
        "port": 8998,
        "db": str(db),
        "run_id": RUN_ID,
    })
    assert arm["funnel"] == {
        "quoted": 0,
        "filled": 0,
        "merged": 0,
        "merged_legs": 0,
        "stop_loss": 0,
    }
    assert arm["stop_loss_exits_count"] == 0

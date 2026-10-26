from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path
from scripts.reconcile_tournament_stack import (
    sweep_stale_orders,
    is_port_listening,
)


def test_sweep_stale_orders_purges_matching_orders(tmp_path):
    db_file = tmp_path / "test_arm.db"
    con = sqlite3.connect(str(db_file))
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            condition_id TEXT,
            status TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            size REAL
        )
    """)
    # Insert clean order
    cur.execute("INSERT INTO orders (order_id, condition_id, status) VALUES ('clean-1', 'active-market-1', 'FILLED')")
    cur.execute("INSERT INTO fills (order_id, size) VALUES ('clean-1', 10.0)")
    # Insert stale order
    cur.execute("INSERT INTO orders (order_id, condition_id, status) VALUES ('stale-1', 'bra-san-bah-2026-10-24-bah', 'FILLED')")
    cur.execute("INSERT INTO fills (order_id, size) VALUES ('stale-1', 10.0)")
    con.commit()
    con.close()

    removed = sweep_stale_orders(db_file, ["bra-san-bah-2026-10-24%"])
    assert removed == 1

    con2 = sqlite3.connect(str(db_file))
    orders = con2.execute("SELECT order_id FROM orders").fetchall()
    fills = con2.execute("SELECT order_id FROM fills").fetchall()
    con2.close()

    assert len(orders) == 1
    assert orders[0][0] == "clean-1"
    assert len(fills) == 1
    assert fills[0][0] == "clean-1"


def test_sweep_stale_orders_on_clean_db(tmp_path):
    db_file = tmp_path / "clean_arm.db"
    con = sqlite3.connect(str(db_file))
    cur = con.cursor()
    cur.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, order_id TEXT, condition_id TEXT)")
    cur.execute("INSERT INTO orders (order_id, condition_id) VALUES ('ord-1', 'valid-market')")
    con.commit()
    con.close()

    removed = sweep_stale_orders(db_file, ["bra-san-bah-2026-10-24%"])
    assert removed == 0


def test_sweep_without_a_named_slug_deletes_nothing(tmp_path):
    """The reconciler must never purge a market the operator did not name."""
    db_file = tmp_path / "arm.db"
    con = sqlite3.connect(str(db_file))
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, order_id TEXT, condition_id TEXT)")
    con.execute("INSERT INTO orders (order_id, condition_id) VALUES ('ord-1', 'bra-san-bah-2026-10-24-bah')")
    con.commit()
    con.close()

    assert sweep_stale_orders(db_file, []) == 0
    assert sweep_stale_orders(db_file, ["some-other-market%"]) == 0

    con = sqlite3.connect(str(db_file))
    assert con.execute("SELECT count(*) FROM orders").fetchone()[0] == 1
    con.close()

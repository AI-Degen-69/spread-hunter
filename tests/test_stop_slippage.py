"""What a hard-stop exit actually cost, measured off the exit record. #730

Three states the aggregate must never conflate: a fill that matched the floor
(`0.00`, a real measurement), a fill that landed below it (a cost), and an
exit nobody observed filling (unmeasured -- never zero). The queue gate made
this same distinction; this file holds the exit path to it.
"""
import sqlite3

import pytest

from core_brain.order_registry import CloseRecord, OrderRegistry, init_db


# The `closes` table as it shipped before #730: no submitted_floor, no
# fill_source. A store of this shape is what an operator's data/orders.db
# looked like the day this landed.
LEGACY_CLOSES_DDL = """
CREATE TABLE closes (
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
    reason TEXT,
    trigger_bid REAL,
    fresh_bid REAL
);
"""


def _legacy_store(path):
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_CLOSES_DDL)
    conn.execute(
        "INSERT INTO closes (ts, condition_id, method, dn_price, shares, "
        "run_id, reason) VALUES (1.0, '0xold', 'single_buy_exit', 0.15, 5.0, "
        "'r-old', 'lifecycle_hard_stop')"
    )
    conn.commit()
    conn.close()


def test_registry_legacy_store_gains_the_stop_columns_and_old_rows_read_none(tmp_path):
    db = tmp_path / "old.db"
    _legacy_store(db)

    reg = OrderRegistry(db_path=db)

    closes = reg.get_all_closes()
    assert len(closes) == 1
    assert closes[0]["submitted_floor"] is None
    assert closes[0]["fill_source"] is None


def test_registry_repeat_init_is_safe(tmp_path):
    db = tmp_path / "repeat.db"

    init_db(db)
    init_db(db)
    reg = OrderRegistry(db_path=db)

    assert reg.get_all_closes() == []


def test_registry_close_carries_the_floor_and_the_fill_source(tmp_path):
    db = tmp_path / "close.db"
    init_db(db)
    reg = OrderRegistry(db_path=db)

    reg.log_close(CloseRecord(
        ts=1.0, condition_id="0xabc", method="single_buy_exit",
        shares=5.0, dn_price=0.13, cost_basis=3.0, proceeds=0.65,
        realized_pnl=-2.35, reason="lifecycle_hard_stop",
        trigger_bid=0.15, fresh_bid=0.13,
        submitted_floor=0.13, fill_source="simulated",
    ))

    row = reg.get_all_closes()[0]
    assert row["submitted_floor"] == pytest.approx(0.13)
    assert row["fill_source"] == "simulated"


def test_registry_refusals_round_trip_and_survive_a_lifecycle_reset(tmp_path):
    db = tmp_path / "refusals.db"
    init_db(db)
    reg = OrderRegistry(db_path=db)

    reg.log_hard_stop_refusal(
        pair_id="pair-1", condition_id="0xabc", trigger_bid=0.15,
        outcome="refused", detail="No bid on tok: nothing to sell into.")
    reg.log_hard_stop_refusal(
        pair_id="pair-1", condition_id="0xabc", trigger_bid=0.15,
        outcome="refused", detail="No bid on tok: nothing to sell into.")

    rows = reg.get_hard_stop_refusals()
    assert [r["outcome"] for r in rows] == ["refused", "refused"]
    assert rows[0]["pair_id"] == "pair-1"
    assert rows[0]["trigger_bid"] == pytest.approx(0.15)
    assert rows[0]["run_id"]

    generation = reg.begin_lifecycle_reset("0xabc", "test", now_ms=1000)
    assert reg.finish_lifecycle_reset("0xabc", generation, reset_ts_ms=2000)

    # Reset clears lifecycle rows only; refusal evidence is append-only.
    assert len(reg.get_hard_stop_refusals()) == 2

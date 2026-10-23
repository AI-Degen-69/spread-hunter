"""Read-only contract tests for core_brain.fill_quality_report (#761).

Station III Task 1: the report opens evidence read-only, never writes, never
repairs. These tests fail without the module and pass against the skeleton.
"""

import hashlib
import re
import sqlite3
from pathlib import Path

import core_brain.fill_quality_report as fqr


def _write_ddl(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE orders (
            id TEXT PRIMARY KEY, order_id TEXT UNIQUE, condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL, side TEXT NOT NULL, price REAL NOT NULL,
            original_size REAL NOT NULL, status TEXT NOT NULL,
            posted_ts INTEGER NOT NULL, last_polled_ts INTEGER NOT NULL,
            pair_id TEXT, max_pair_cost_at_post REAL, run_id TEXT
        );
        CREATE TABLE fills (
            trade_id TEXT PRIMARY KEY, order_uuid TEXT NOT NULL, size REAL NOT NULL,
            price REAL NOT NULL, venue_ts INTEGER, recorded_ts INTEGER, run_id TEXT
        );
        CREATE TABLE quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, market_slug TEXT,
            condition_id TEXT, token_id TEXT, side TEXT, price REAL, size REAL,
            queue_ahead REAL, mid REAL, edge_vs_mid REAL, t_remaining REAL,
            filled REAL, fill_ts REAL
        );
        CREATE TABLE closes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, condition_id TEXT,
            market_slug TEXT, method TEXT, gas REAL, shares REAL, up_price REAL,
            dn_price REAL, cost_basis REAL, proceeds REAL, fee REAL,
            realized_pnl REAL, forgone_vs_settlement REAL, up_cost_removed REAL,
            dn_cost_removed REAL, tx_hash TEXT, run_id TEXT, trigger_bid REAL,
            fresh_bid REAL, reason TEXT, submitted_floor REAL, fill_source TEXT
        );
        CREATE TABLE resolutions (
            condition_id TEXT PRIMARY KEY, winning_token TEXT,
            winning_token_id TEXT, resolved_ts REAL, run_id TEXT
        );
        CREATE TABLE single_leg_lifecycle (
            pair_id TEXT PRIMARY KEY, condition_id TEXT NOT NULL, state TEXT NOT NULL,
            reason TEXT NOT NULL, updated_ts_ms INTEGER NOT NULL, evidence_json TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_source_contains_no_write_statements():
    """The module must never open a transaction: no write SQL, no commit."""
    src = Path(fqr.__file__).read_text(encoding="utf-8")
    assert not re.search(
        r"\.execute\(\s*[\"'](?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b",
        src,
        re.IGNORECASE,
    )
    assert ".commit()" not in src
    # Named in prose (as in fill_calibration.py); never imported or built.
    assert "OrderRegistry(" not in src
    assert "from core_brain" not in src
    assert "import core_brain" not in src


def test_hash_in_path_opens_the_intended_file(tmp_path: Path):
    """A `#` in the filename must not truncate the mode=ro URI (which would
    create a sibling file read-write). Mirrors the fill_calibration guard."""
    db = tmp_path / "a#b.db"
    _write_ddl(db)
    before = {p.name for p in tmp_path.iterdir()}
    store = fqr.load_store(db, run_id=None, label="shadow")
    assert store.status in (fqr.OK, fqr.NO_DATA)
    assert {p.name for p in tmp_path.iterdir()} == before


def test_inputs_are_not_modified(tmp_path: Path):
    """Running the report leaves the evidence bit-identical (mtime + hash)."""
    db = tmp_path / "shadow.db"
    _write_ddl(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO orders (id, condition_id, token_id, side, price,"
        " original_size, status, posted_ts, last_polled_ts)"
        " VALUES ('o1', 'c1', 't1', 'BUY', 0.60, 10.0, 'filled', 1, 1)"
    )
    conn.commit()
    conn.close()
    mtime, digest = db.stat().st_mtime_ns, _sha(db)
    fqr.build_report(fqr.load_store(db, run_id=None, label="shadow"))
    assert (db.stat().st_mtime_ns, _sha(db)) == (mtime, digest)


def test_missing_path_is_no_data(tmp_path: Path):
    store = fqr.load_store(tmp_path / "absent.db", run_id=None, label="shadow")
    assert store.status == fqr.NO_DATA


# --- Task 2: fill -> book -> lifecycle join ---------------------------------

FILL_MS = 1_791_000_000_000
FILL_SEC = FILL_MS / 1000.0


def _seed_order(conn, oid, cond, tok, side, price, size, status, pair, run="r1",
                post_ms=FILL_MS):
    conn.execute(
        "INSERT INTO orders (id, condition_id, token_id, side, price,"
        " original_size, status, posted_ts, last_polled_ts, pair_id, run_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, cond, tok, side, price, size, status, post_ms, post_ms, pair, run),
    )


def _seed_fill(conn, tid, oid, size, price, run="r1", ts_ms=FILL_MS):
    conn.execute(
        "INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts,"
        " recorded_ts, run_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tid, oid, size, price, ts_ms, ts_ms, run),
    )


def _seed_quote(conn, cond, tok, side, price, ts_sec, mid=None, edge=None,
                queue=None, size=50.0):
    conn.execute(
        "INSERT INTO quotes (ts, condition_id, token_id, side, price, size,"
        " queue_ahead, mid, edge_vs_mid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts_sec, cond, tok, side, price, size, queue, mid, edge),
    )


def _seed_close(conn, cond, method, pnl, reason=None, run="r1"):
    conn.execute(
        "INSERT INTO closes (ts, condition_id, method, realized_pnl, reason,"
        " run_id) VALUES (?, ?, ?, ?, ?, ?)",
        (FILL_SEC + 60.0, cond, method, pnl, reason, run),
    )


def _seeded_db(tmp_path: Path):
    db = tmp_path / "seed.db"
    _write_ddl(db)
    conn = sqlite3.connect(db)
    # p1/c1: merged pair, two fills on one order (pro-rata PnL).
    _seed_order(conn, "o1", "c1", "tU", "BUY", 0.60, 10.0, "filled", "p1")
    _seed_fill(conn, "f1a", "o1", 4.0, 0.60)
    _seed_fill(conn, "f1b", "o1", 6.0, 0.60)
    _seed_quote(conn, "c1", "tU", "UP", 0.60, FILL_SEC - 30,
                mid=0.59, edge=0.01, queue=100.0)
    _seed_quote(conn, "c1", "tU", "UP", 0.61, FILL_SEC + 30,
                mid=0.60, edge=0.01, queue=10.0)
    _seed_quote(conn, "c1", "tD", "DOWN", 0.38, FILL_SEC - 5, mid=0.39)
    _seed_close(conn, "c1", "shadow_merge", 3.29)
    # p2/c2: dead leg via hard stop.
    _seed_order(conn, "o2", "c2", "tU", "BUY", 0.70, 5.0, "filled", "p2")
    _seed_fill(conn, "f2", "o2", 5.0, 0.70)
    _seed_close(conn, "c2", "single_buy_exit", -2.924,
                reason="lifecycle_hard_stop")
    # p3/c3: aged-out rescue.
    _seed_order(conn, "o3", "c3", "tU", "BUY", 0.55, 2.0, "filled", "p3")
    _seed_fill(conn, "f3", "o3", 2.0, 0.55)
    _seed_close(conn, "c3", "single_buy_exit", -0.20,
                reason="aged_out_rescue")
    # p4/c4: open, no close yet.
    _seed_order(conn, "o4", "c4", "tU", "BUY", 0.50, 1.0, "filled", "p4")
    _seed_fill(conn, "f4", "o4", 1.0, 0.50)
    # o5: cancelled, never filled -> no row.
    _seed_order(conn, "o5", "c5", "tU", "BUY", 0.50, 1.0, "cancelled", "p5")
    # other run -> excluded by run filter.
    _seed_order(conn, "o9", "c9", "tU", "BUY", 0.50, 1.0, "filled", "p9",
                run="r2")
    _seed_fill(conn, "f9", "o9", 1.0, 0.50, run="r2")
    conn.commit()
    conn.close()
    return db


def _rows(tmp_path: Path):
    db = _seeded_db(tmp_path)
    store = fqr.load_store(db, run_id="r1", label="shadow")
    assert store.status == fqr.OK
    return fqr.build_report(store).fills


def test_one_row_per_fill_and_no_row_without_fill(tmp_path: Path):
    rows = _rows(tmp_path)
    by_trade = {r.trade_id: r for r in rows}
    assert {"f1a", "f1b", "f2", "f3", "f4"} == set(by_trade)
    assert "f9" not in by_trade  # other run filtered
    assert by_trade["f1a"].pair_key == "p1"


def test_book_state_is_nearest_quote_at_or_before_fill(tmp_path: Path):
    rows = {r.trade_id: r for r in _rows(tmp_path)}
    row = rows["f1a"]
    assert row.mid == 0.59
    assert row.edge_vs_mid == 0.01
    assert row.queue_ahead == 100.0  # the post-fill 10.0 quote is ignored
    assert row.opp_price == 0.38
    assert row.implied_pair_cost == abs(0.60 + 0.38)


def test_outcome_labels(tmp_path: Path):
    rows = {r.trade_id: r for r in _rows(tmp_path)}
    assert rows["f1a"].outcome == "merged"
    assert rows["f2"].outcome == "dead_leg"
    assert rows["f3"].outcome == "aged_out_rescue"
    assert rows["f4"].outcome == "open"


def test_pnl_attribution_is_pro_rata(tmp_path: Path):
    rows = {r.trade_id: r for r in _rows(tmp_path)}
    assert rows["f1a"].attr_pnl == abs(3.29 * 4.0 / 10.0)
    assert rows["f1b"].attr_pnl == abs(3.29 * 6.0 / 10.0)
    assert rows["f2"].attr_pnl == -abs(2.924)

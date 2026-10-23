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
            pair_id TEXT, max_pair_cost_at_post REAL
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

"""Shadow rehearsal on 5-min ladders (issue #324): placement + fill + exit.

A ladder-mimic ``decide_fn`` posts the probe-winning shape (2 rungs/side)
through the real shadow seam against a deterministic two-market fixture:
MKT-PAIR fills both legs (merge path), MKT-SOLO fills one leg (exit path).

What is pinned here, in dependency order:

* rungs rest under ONE ``pair_id`` per market (the seam mints a fresh pair
  per submit call unless every intent carries the same ``pair_id``);
* fills credit oldest-first and conserve shares (no orphans, no double-count);
* the balanced market merges at parity;
* the one-leg residue exits inside the window with no orphaned fills;
* nothing in the run can sign: shadow ids only, production store refused.

All network is stubbed: tape-driven trades installed by the harness itself,
the resolution sweep patched out. ``tmp_path`` stores only.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from scripts.ladder_shadow_rehearsal import (
    RehearsalRefused,
    build_fixture_series,
    ladder_decide,
    ladder_pair_id,
    load_tape_markets,
    refuse_output,
    run_rehearsal,
)


@pytest.fixture
def no_network(monkeypatch):
    """The rehearsal's own sweep reads the venue; a rehearsal never should."""
    monkeypatch.setattr(
        "core_brain.market_resolution.sweep_market_resolutions",
        lambda *a, **k: iter([]))


@pytest.fixture
def shipped_defaults(monkeypatch):
    """Pin the shipped exit knobs the rehearsal relies on (CI is clean)."""
    monkeypatch.setenv("HUNTER_SINGLE_BUY_GRACE_SEC", "0")


@pytest.fixture
def rehearsed(tmp_path, no_network, shipped_defaults):
    db = tmp_path / "ladder_shadow.db"
    out = tmp_path / "ladder_shadow.json"
    report = run_rehearsal(series="fixture-5m",
                           tape_markets=build_fixture_series(),
                           db_path=db, out_path=out,
                           shape="2", rotations=8,
                           run_id="ladder-fixture")
    return db, out, report


def _rows(db: Path, sql: str, args=()):
    from core_brain.order_registry import get_connection
    with closing(get_connection(db)) as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# --- T1: the mimic posts the shape -------------------------------------------

def test_decide_posts_two_rungs_per_side_at_distinct_prices():
    decide = ladder_decide("s", (0.45, 0.55),
                           {"tok-a": "UP", "tok-b": "DOWN"})

    class Cfg:
        min_quote_shares = 5

    up_book = {"token_id": "tok-a", "condition_id": "cid-1",
               "best_bid": 0.50, "best_ask": 0.52}
    down_book = {"token_id": "tok-b", "condition_id": "cid-1",
                 "best_bid": 0.50, "best_ask": 0.52}
    intents, why = decide(Cfg(), up_book, down_book, None, 3600.0, None)

    assert len(intents) == 4
    up_px = sorted(i.price for i in intents if i.side == "UP")
    dn_px = sorted(i.price for i in intents if i.side == "DOWN")
    assert up_px == [0.45, 0.55]
    assert dn_px == [0.45, 0.55]
    assert {i.pair_id for i in intents} == {ladder_pair_id("s", "cid-1")}


# --- T2: placement + fill attribution ----------------------------------------

def test_rungs_rest_under_one_pair_id_per_market(rehearsed):
    db, _, report = rehearsed
    assert report["placements"]["one_pair_id_per_market"] is True
    assert sorted(report["pair_ids"]) == [
        ladder_pair_id("fixture-5m", "fixture-pair"),
        ladder_pair_id("fixture-5m", "fixture-solo"),
    ]
    orders = _rows(db, "SELECT condition_id, pair_id FROM orders")
    assert orders, "the rehearsal must rest rungs before asserting on them"
    for o in orders:
        assert o["pair_id"] == ladder_pair_id("fixture-5m", o["condition_id"])


def test_unstamped_intents_split_pairs_which_is_why_we_stamp(tmp_path, no_network):
    """Pins the seam semantic the stamp relies on: no carry, fresh pair."""
    from core_brain.order_registry import OrderRegistry, init_db
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit
    from core_brain.quotes import QuoteIntent

    db = tmp_path / "seam.db"
    init_db(db)
    ensure_shadow_tables(db)
    reg = OrderRegistry(db_path=db, run_id="seam-probe")

    class Mkt:
        condition_id = "cid-x"
        market_slug = "m"

    class Cfg:
        max_pair_cost = 0.99

    book = {"bids": {}, "asks": {}}
    intents = [QuoteIntent(side="UP", token_id="t1", price=0.45, size=5,
                           mid=0.45, edge_vs_mid=0.0),
               QuoteIntent(side="DOWN", token_id="t2", price=0.45, size=5,
                           mid=0.45, edge_vs_mid=0.0)]
    assert record_submit(None, reg, Mkt(), intents, Cfg(), db_path=db,
                         book_fn=lambda h, t: book) == 2
    assert record_submit(None, reg, Mkt(), intents, Cfg(), db_path=db,
                         book_fn=lambda h, t: book) == 2
    pairs = {r["pair_id"] for r in _rows(db, "SELECT DISTINCT pair_id FROM orders")}
    assert len(pairs) == 2, "two submit calls mint two pairs without the stamp"


def test_fills_are_oldest_first_and_conserved(rehearsed):
    _, _, report = rehearsed
    assert report["fills"]["n"] > 0, "an unfilled rehearsal proves nothing"
    assert report["fills"]["oldest_first"] is True
    cons = report["conservation"]
    assert cons["orphan_fills"] == 0
    assert cons["double_counted_fills"] == 0


# --- T3: exit paths + zero-live proof + report --------------------------------

def test_balanced_market_merges_at_parity(rehearsed):
    db, _, report = rehearsed
    assert report["exits"]["merges"] >= 1
    assert report["exits"]["merged_shares"] > 0
    merges = _rows(db, "SELECT * FROM closes WHERE method = 'shadow_merge'")
    assert merges, "the pair fill must close as a merge, not vanish"


def test_one_leg_residue_exits_inside_window_with_no_orphan(rehearsed):
    db, _, report = rehearsed
    assert report["exits"]["single_exits"] >= 1
    assert report["exits"]["exited_shares"] > 0

    orders = _rows(db, "SELECT * FROM orders WHERE condition_id = 'fixture-solo'")
    by_id = {o["id"]: o for o in orders}
    filled: dict[str, float] = {}
    for f in _rows(db, "SELECT * FROM fills"):
        if f.get("order_uuid") in by_id:
            filled[f["order_uuid"]] = filled.get(f["order_uuid"], 0.0) + float(f.get("size") or 0.0)
    filled_shares = sum(filled.values())
    assert filled_shares > 0, "the solo market must fill its one leg to test the exit"

    closes = _rows(db, "SELECT * FROM closes WHERE condition_id = 'fixture-solo'")
    solo_exited = sum(float(c.get("shares") or 0.0) for c in closes
                      if c.get("method") == "single_buy_exit")
    solo_merged = sum(float(c.get("shares") or 0.0) for c in closes
                      if c.get("method") == "shadow_merge")
    # Every filled share is accounted for by a close: nothing sits orphaned.
    assert filled_shares <= solo_exited + solo_merged + 1e-9


def test_nothing_in_the_run_can_sign(rehearsed):
    db, _, report = rehearsed
    assert report["live_execution"] is False
    orders = _rows(db, "SELECT order_id FROM orders")
    assert orders
    assert all(o["order_id"].startswith("shadow-") for o in orders)
    assert not _rows(db, "SELECT tx_hash FROM closes WHERE tx_hash LIKE '0x%'")


def test_production_store_is_refused(tmp_path, no_network, shipped_defaults):
    from core_brain.order_registry import DEFAULT_DB_PATH
    from core_brain.shadow_guard import assert_not_production_registry
    # BaseException by design: the loop's `except Exception` must not swallow it.
    with pytest.raises(BaseException):
        assert_not_production_registry(DEFAULT_DB_PATH)
    # The guard runs before any table exists, so the store is untouched.
    with pytest.raises(BaseException):
        run_rehearsal(series="fixture-5m", tape_markets=build_fixture_series(),
                      db_path=DEFAULT_DB_PATH, out_path=None,
                      shape="2", rotations=2)


def test_tape_loader_reads_both_legs_inside_the_window(tmp_path):
    """The operator path (real series tapes) loads through this function."""
    import sqlite3
    db = tmp_path / "tape.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE markets (token_id TEXT PRIMARY KEY, condition_id TEXT NOT NULL,"
        " question TEXT NOT NULL, slug TEXT NOT NULL, volume_24h REAL NOT NULL,"
        " first_seen INTEGER NOT NULL, up_wins INTEGER)")
    con.execute(
        "CREATE TABLE ticks (token_id TEXT NOT NULL, ts INTEGER NOT NULL,"
        " price REAL NOT NULL, PRIMARY KEY (token_id, ts))")
    base = 1_700_000_000
    con.executemany("INSERT INTO markets VALUES (?,?,?,?,?,?,?)", [
        ("tok-a", "cid-1", "q", "btc-updown-5m-1", 0.0, base, 1),
        ("tok-b", "cid-1", "q", "btc-updown-5m-1", 0.0, base, 1),
        ("tok-c", "cid-2", "q", "btc-updown-5m-2", 0.0, base, 0),
    ])
    con.executemany("INSERT INTO ticks VALUES (?,?,?)", [
        ("tok-a", base + 1, 0.55), ("tok-a", base + 400, 0.44),
        ("tok-b", base + 1, 0.56), ("tok-b", base + 400, 0.45),
        ("tok-c", base + 1, 0.50),
    ])
    con.commit()
    with con:
        markets = load_tape_markets(con, "btc-updown-5m-%", 300)
    assert [m.condition_id for m in markets] == ["cid-1"]
    only = markets[0]
    assert only.leg_a.token_id == "tok-a"
    assert [p for _, p in only.leg_a.ticks] == [0.55]
    assert [p for _, p in only.leg_b.ticks] == [0.56]
    con.close()


def test_refuse_output_blocks_production_names_and_live_dirs(tmp_path):
    with pytest.raises(RehearsalRefused):
        refuse_output(tmp_path / "orders.db")
    with pytest.raises(RehearsalRefused):
        refuse_output(Path("data/report.json"))
    with pytest.raises(RehearsalRefused):
        refuse_output(Path("run/report.json"))
    assert refuse_output(tmp_path / "ladder_shadow.json").name == "ladder_shadow.json"


def test_refused_out_aborts_before_any_store_byte(tmp_path, no_network,
                                                  shipped_defaults):
    db = tmp_path / "never_created.db"
    with pytest.raises(RehearsalRefused):
        run_rehearsal(series="fixture-5m", tape_markets=build_fixture_series(),
                      db_path=db, out_path=tmp_path / "orders.db",
                      shape="2", rotations=2)
    assert not db.exists(), "a refused report must stop before run_shadow"


def test_db_out_alias_is_refused(tmp_path, no_network, shipped_defaults):
    db = tmp_path / "same.db"
    with pytest.raises(RehearsalRefused):
        run_rehearsal(series="fixture-5m", tape_markets=build_fixture_series(),
                      db_path=db, out_path=db,
                      shape="2", rotations=2)
    assert not db.exists(), "the store must not be created, let alone truncated"


def test_report_covers_both_series_labels(tmp_path, no_network, shipped_defaults):
    """The operator runs one harness per series; both reports share the schema."""
    required = {"issue", "series", "shape", "rungs", "markets", "pair_ids",
                "placements", "fills", "exits", "conservation", "live_execution"}
    for series in ("btc-up-or-down-5m", "eth-up-or-down-5m"):
        db = tmp_path / f"ladder_{series}.db"
        out = tmp_path / f"ladder_{series}.json"
        report = run_rehearsal(series=series, tape_markets=build_fixture_series(),
                               db_path=db, out_path=out,
                               shape="2", rotations=8)
        assert required <= set(report), f"missing keys for {series}"
        assert report["issue"] == 324
        assert report["live_execution"] is False
        assert report["placements"]["one_pair_id_per_market"] is True
        assert report["fills"]["n"] > 0

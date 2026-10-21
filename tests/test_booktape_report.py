"""The book/tape report reads only live tape, and names what the cap refuses."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts import book_tape_recorder as btr
from scripts import booktape_report as rpt


def _store(tmp_path: Path, samples: list[dict], tape: list[tuple]) -> Path:
    """A store with the given book samples and (ticks, volume, bootstrap) tape."""
    db = tmp_path / "booktape.db"
    conn = btr.open_store(db)
    try:
        for i, s in enumerate(samples):
            row = {"ts": float(i), "run_id": "r", "condition_id": "0xabc",
                   "market_slug": "mlb-a-b", "tick": 0.01,
                   "best_bid_up": 0.42, "best_ask_up": 0.43,
                   "best_bid_down": 0.57, "best_ask_down": 0.58,
                   "mid_up": 0.425, "mid_down": 0.575, "mid_sum": 1.0,
                   "touch_pair_cost": 0.99,
                   "touch_size_up": 800.0, "touch_size_down": 700.0}
            row.update(s)
            btr.write_sample(conn, row)
        for ticks, volume, boot in tape:
            conn.execute(
                "INSERT INTO tape_buckets (ts, run_id, condition_id, market_slug,"
                " token_id, side, price, ticks_from_mid, volume, is_bootstrap)"
                " VALUES (0,'r','0xabc','mlb-a-b','tok','UP',0.42,?,?,?)",
                (ticks, volume, boot))
        conn.commit()
    finally:
        conn.close()
    return db


def test_reachability_excludes_bootstrap_rows(tmp_path):
    # Arrange -- 900 shares of bootstrap tape at an impossible distance, and
    # 100 of live tape one tick under mid.
    db = _store(tmp_path, [{}], [(-27, 900.0, 1), (1, 100.0, 0)])

    # Act
    conn = sqlite3.connect(db)
    try:
        rows = rpt.reachability(conn)
    finally:
        conn.close()

    # Assert -- only the live row survives
    assert rows == [(1, 100.0, 1)]


def test_report_counts_bootstrap_rows_separately(tmp_path):
    db = _store(tmp_path, [{}], [(-27, 900.0, 1), (1, 100.0, 0)])

    text = rpt.report(db)

    assert "1 live tape rows, 1 bootstrap rows excluded" in text


def test_report_marks_a_touch_pair_at_the_cap_as_refused(tmp_path):
    # A $0.99 touch pair is the cheapest two resting bids can make on a 1c
    # book, and `risk.hard_block` refuses it with `>=`.
    db = _store(tmp_path, [{"touch_pair_cost": 0.99}], [])

    text = rpt.report(db)

    assert "$0.9900" in text
    assert "REFUSED by bot_pos_price_max_pair_cost_usd" in text


def test_report_marks_a_cheaper_touch_pair_as_tradeable(tmp_path):
    db = _store(tmp_path, [{"touch_pair_cost": 0.85, "mid_sum": 0.96}], [])

    text = rpt.report(db)

    assert "$0.8500" in text
    assert "tradeable" in text


def test_reachable_share_counts_only_volume_at_or_below_mid(tmp_path):
    # 300 above mid can never reach a resting buy; 100 at one tick under can.
    db = _store(tmp_path, [{}], [(-1, 300.0, 0), (1, 100.0, 0)])

    text = rpt.report(db)

    assert "reachable by a bid 1 tick(s) under mid:  25.00%" in text


def test_reachable_share_excludes_prints_nearer_mid_than_the_bid(tmp_path):
    # 300 shares printed AT mid filled better-priced bids and never reached a
    # bid resting one tick under; only the 100 at that tick did. Accumulating
    # from mid down instead would report 100% here.
    db = _store(tmp_path, [{}], [(0, 300.0, 0), (1, 100.0, 0)])

    text = rpt.report(db)

    assert "reachable by a bid 0 tick(s) under mid: 100.00%" in text
    assert "reachable by a bid 1 tick(s) under mid:  25.00%" in text


def test_reachability_cumulative_column_is_a_tail(tmp_path):
    # The `cum>=` column at tick k is the volume a bid at k could be reached
    # by, so it must fall as k grows, never rise.
    db = _store(tmp_path, [{}], [(0, 300.0, 0), (1, 100.0, 0), (2, 10.0, 0)])

    lines = rpt.report(db).splitlines()
    head = next(i for i, ln in enumerate(lines) if "cum>=" in ln)
    shares = [ln.split()[-1] for ln in lines[head + 1:head + 4]]

    assert shares == ["100.00%", "26.83%", "2.44%"]


def test_report_survives_a_store_with_no_tape(tmp_path):
    db = _store(tmp_path, [{}], [])

    text = rpt.report(db)

    assert "all tape:" in text


def test_spreads_skip_a_one_sided_book(tmp_path):
    # A book with no ask has no spread to average. Including it would have the
    # report format None, and averaging the readable rows under a count that
    # includes the dark ones reports a spread for a market that was half dark.
    db = _store(tmp_path, [{}, {"best_ask_up": None}], [])

    conn = sqlite3.connect(db)
    try:
        rows = rpt.spreads(conn)
    finally:
        conn.close()

    assert rows == [("mlb-a-b", 1, 0.01, 0.01, 800.0, 700.0)]


def test_report_renders_when_every_book_is_one_sided(tmp_path):
    db = _store(tmp_path, [{"best_ask_up": None, "best_ask_down": None}], [])

    text = rpt.report(db)

    assert "MARKETS SEEN" in text

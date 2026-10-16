"""Recovering every grace value from one run held at the longest one."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts import grace_sweep_report as gsr


def _shadow(tmp_path: Path, fills) -> Path:
    """fills: (ts_sec, cid, side, size, price, pair_id).

    Mirrors the real store on the three things the report has to get right:
    `fills.order_uuid` holds `orders.id`, the UP/DOWN leg is carried by the
    token rather than by `orders.side` (which is always BUY), and
    `recorded_ts` is in milliseconds.
    """
    db = tmp_path / "shadow.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE fills (order_uuid TEXT, size REAL, price REAL,"
                 " recorded_ts REAL)")
    conn.execute("CREATE TABLE orders (id TEXT, order_id TEXT,"
                 " condition_id TEXT, side TEXT, token_id TEXT, pair_id TEXT)")
    conn.execute("CREATE TABLE quotes (token_id TEXT, side TEXT)")
    seen_tokens = set()
    for i, row in enumerate(fills):
        ts, cid, side, size, price, pair_id = row
        oid, token = f"o{i}", f"{cid}-{side}"
        conn.execute("INSERT INTO orders VALUES (?,?,?,?,?,?)",
                     (oid, f"shadow-{i}", cid, "BUY", token, pair_id))
        conn.execute("INSERT INTO fills VALUES (?,?,?,?)",
                     (oid, size, price, ts * 1000.0))
        if token not in seen_tokens:
            conn.execute("INSERT INTO quotes VALUES (?,?)", (token, side))
            seen_tokens.add(token)
    conn.commit()
    conn.close()
    return db


def _book(tmp_path: Path, samples) -> Path:
    """samples: (ts, cid, bid_up, bid_down)."""
    db = tmp_path / "book.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE book_samples (ts REAL, condition_id TEXT,"
                 " best_bid_up REAL, best_bid_down REAL)")
    conn.executemany("INSERT INTO book_samples VALUES (?,?,?,?)", samples)
    conn.commit()
    conn.close()
    return db


def _rows(text: str) -> dict[int, list[str]]:
    """The horizon rows of the table, keyed by grace seconds."""
    return {int(float(ln.split()[0])): ln.split()
            for ln in text.splitlines()
            if ln.strip()[:1].isdigit() and "sh" not in ln.split()[0]}


def _ev(pair_id, ts, side, cid="m", size=5.0, price=0.66):
    return {"pair_id": pair_id, "ts": ts, "side": side, "cid": cid,
            "size": size, "price": price}


# --- pairing ---------------------------------------------------------------


def test_a_companion_fill_is_matched_within_its_own_pair():
    tls = gsr.pair_timelines([_ev("p1", 100.0, "UP"), _ev("p1", 130.0, "DOWN")])

    assert len(tls) == 1
    assert tls[0]["open_side"] == "UP"
    assert gsr.first_companion_gap(tls[0]) == pytest.approx(30.0)


def test_a_pair_whose_companion_never_fills_has_no_gap():
    # These are the denominator. No grace value rescues them, and dropping
    # them would inflate every rescue rate.
    tls = gsr.pair_timelines([_ev("p1", 100.0, "UP")])

    assert gsr.first_companion_gap(tls[0]) is None
    assert tls[0]["open_qty"] == 5.0


def test_pairs_never_marry_across_pair_ids():
    # The bug this replaced: matching the next opposite-side fill in the same
    # market married legs the engine never grouped, and on the real run that
    # produced a phantom $1.15 pair -- over the dollar the instrument pays,
    # and impossible, because the risk gate had refused exactly that.
    tls = gsr.pair_timelines([
        _ev("p1", 100.0, "UP", price=0.42),
        _ev("p2", 101.0, "DOWN", price=0.73),
    ])

    assert len(tls) == 2
    assert all(gsr.first_companion_gap(t) is None for t in tls)


def test_a_same_side_refill_is_not_a_companion():
    # Adding to the leg we already hold is not the other side of the pair.
    tls = gsr.pair_timelines([_ev("p1", 100.0, "UP"), _ev("p1", 110.0, "UP")])

    assert gsr.first_companion_gap(tls[0]) is None
    assert tls[0]["open_qty"] == 10.0


# --- quantity matching ----------------------------------------------------


def test_only_the_matched_quantity_merges_the_rest_is_exposed(tmp_path):
    # UP opens 5, DOWN only ever fills 1: 1 share merges, 4 stay exposed.
    # The pre-review code counted all 5 as rescued.
    db = _shadow(tmp_path, [
        (100.0, "m", "UP", 5.0, 0.60, "p1"),
        (101.0, "m", "DOWN", 1.0, 0.39, "p1"),
    ])

    rows = _rows(gsr.sweep(db, None))

    assert float(rows[15][1]) == 1.0   # merged sh
    assert float(rows[15][2]) == 4.0   # exposed sh


def test_a_companion_that_dribbles_in_merges_only_what_arrived_by_the_horizon(tmp_path):
    db = _shadow(tmp_path, [
        (100.0, "m", "UP", 6.0, 0.60, "p1"),
        (110.0, "m", "DOWN", 2.0, 0.39, "p1"),
        (200.0, "m", "DOWN", 4.0, 0.39, "p1"),
    ])

    rows = _rows(gsr.sweep(db, None))

    assert float(rows[15][1]) == 2.0    # only the first DOWN chunk is in
    assert float(rows[15][2]) == 4.0
    assert float(rows[120][1]) == 6.0   # both chunks caught by 120s
    assert float(rows[120][2]) == 0.0


def test_a_later_same_side_fill_is_not_backdated_into_a_shorter_horizon(tmp_path):
    # UP 5 at t=100, another UP 5 at t=140, DOWN 10 at t=101. The pre-review
    # code summed both UP fills into a quantity dated to t=100, so the 5s row
    # counted the t=140 shares as already open. They start their own grace.
    db = _shadow(tmp_path, [
        (100.0, "m", "UP", 5.0, 0.60, "p1"),
        (140.0, "m", "UP", 5.0, 0.60, "p1"),
        (101.0, "m", "DOWN", 10.0, 0.40, "p1"),
    ])

    rows = _rows(gsr.sweep(db, None))

    assert float(rows[gsr.POLL_INTERVAL_SEC][1]) == 5.0   # only the first chunk is open
    assert float(rows[gsr.POLL_INTERVAL_SEC][2]) == 0.0   # its companion is already there
    assert float(rows[45][1]) == 10.0   # second chunk in by 45s, companion waiting
    assert float(rows[45][2]) == 0.0


# --- the horizons -------------------------------------------------------------


def test_a_longer_grace_contains_the_shorter_ones(tmp_path):
    # The whole reason one run answers every value: a companion at 30s is
    # merged by 45 and 120, not by 15 or the 5s floor.
    db = _shadow(tmp_path, [
        (100.0, "m", "UP", 5.0, 0.66, "p1"),
        (130.0, "m", "DOWN", 5.0, 0.33, "p1"),
    ])

    rows = _rows(gsr.sweep(db, None))

    assert float(rows[gsr.POLL_INTERVAL_SEC][1]) == 0.0
    assert float(rows[15][1]) == 0.0
    assert float(rows[45][1]) == 5.0
    assert float(rows[120][1]) == 5.0


def test_the_floor_row_is_the_poll_interval_not_zero(tmp_path):
    # The live loop exits on the first poll AFTER grace expires, so a grace
    # below the rotation cadence is unreachable. A zero row would recommend
    # against a setting the engine cannot make.
    db = _shadow(tmp_path, [(100.0, "m", "UP", 5.0, 0.66, "p1")])

    text = gsr.sweep(db, None)

    assert gsr.HORIZONS[0] == gsr.POLL_INTERVAL_SEC
    assert 0.0 not in gsr.HORIZONS
    assert "the 5s row is the floor" in text


def test_a_companion_within_one_poll_of_the_horizon_is_flagged_ambiguous(tmp_path):
    # A companion at t+18s with a 15s horizon: the live loop, polling every 5s,
    # may or may not have still held the leg. The report says so rather than
    # forcing the split.
    db = _shadow(tmp_path, [
        (100.0, "m", "UP", 5.0, 0.66, "p1"),
        (118.0, "m", "DOWN", 5.0, 0.33, "p1"),
    ])

    text = gsr.sweep(db, None)

    assert "within one poll of the horizon" in text


# --- exit pricing -------------------------------------------------------------


def test_the_exit_is_priced_at_the_bid_as_it_stood_at_the_horizon(tmp_path):
    # A leg held 120s exits at the 120s bid. Pricing it at the fill-time bid
    # would hide the cost of waiting, which is the whole trade-off.
    db = _shadow(tmp_path, [(100.0, "m", "UP", 10.0, 0.66, "p1")])
    book = _book(tmp_path, [
        (100.0, "m", 0.66, 0.33),
        (115.0, "m", 0.65, 0.34),
        (220.0, "m", 0.60, 0.39),
    ])

    rows = _rows(gsr.sweep(db, book))

    # at 15s the bid is 0.65 -> (0.65-0.66)*10 = -0.10
    assert rows[15][4] == "-0.1000"
    # at 120s the bid is 0.60 -> (0.60-0.66)*10 = -0.60
    assert rows[120][4] == "-0.6000"


def test_zero_exposed_shares_is_a_measured_zero_not_unmeasured(tmp_path):
    # Both legs fill in full inside the floor -> nothing to sell, and the exit
    # cost is a real 0, not missing data.
    db = _shadow(tmp_path, [
        (100.0, "m", "UP", 5.0, 0.55, "p1"),
        (101.0, "m", "DOWN", 5.0, 0.44, "p1"),
    ])

    rows = _rows(gsr.sweep(db, None))

    assert rows[gsr.POLL_INTERVAL_SEC][4] == "0.0000"     # exit $
    assert rows[gsr.POLL_INTERVAL_SEC][5] == "0.0500"     # net $ = 5 merged sh * 1c


def test_a_partial_exit_total_is_withheld_not_published_as_net(tmp_path):
    # Two exposed pairs, only one with a bid in the book: the sum of one is not
    # the exit cost, so exit $ and net $ are withheld.
    db = _shadow(tmp_path, [
        (100.0, "a", "UP", 5.0, 0.60, "p1"),
        (100.0, "b", "UP", 5.0, 0.60, "p2"),
    ])
    book = _book(tmp_path, [(250.0, "a", 0.50, 0.49)])   # only market a, after 120s

    rows = _rows(gsr.sweep(db, book))

    assert rows[120][4] == "1of2priced"
    assert rows[120][5] == "unmeasured"


def test_the_exit_column_is_unmeasured_without_a_book(tmp_path):
    db = _shadow(tmp_path, [(100.0, "m", "UP", 5.0, 0.66, "p1")])

    text = gsr.sweep(db, None)

    assert "unmeasured" in text
    assert "pass --book-db" in text


def test_bid_at_takes_the_first_sample_at_or_after_the_horizon(tmp_path):
    book = gsr._ro(_book(tmp_path, [
        (100.0, "m", 0.66, 0.33),
        (150.0, "m", 0.60, 0.39),
    ]))
    try:
        assert gsr.bid_at(book, "m", "UP", 120.0) == 0.60
        assert gsr.bid_at(book, "m", "UP", 100.0) == 0.66
        assert gsr.bid_at(book, "m", "UP", 999.0) is None
    finally:
        book.close()


# --- companion-wait stats ---------------------------------------------------


def test_the_reported_wait_is_the_true_median_for_an_even_sample(tmp_path):
    # gaps of 1s and 3s -> median 2, not the upper middle value 3.
    db = _shadow(tmp_path, [
        (100.0, "a", "UP", 5.0, 0.6, "p1"), (101.0, "a", "DOWN", 5.0, 0.4, "p1"),
        (100.0, "b", "UP", 5.0, 0.6, "p2"), (103.0, "b", "DOWN", 5.0, 0.4, "p2"),
    ])

    text = gsr.sweep(db, None)

    assert "median 2.0" in text


def test_a_fill_with_no_quote_side_is_a_data_quality_error(tmp_path):
    # `bid_at` would price an unknown side as DOWN and `pair_timelines` could
    # count it as the companion -- refuse the run instead of guessing.
    db = tmp_path / "shadow.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE fills (order_uuid TEXT, size REAL, price REAL,"
                 " recorded_ts REAL)")
    conn.execute("CREATE TABLE orders (id TEXT, order_id TEXT,"
                 " condition_id TEXT, side TEXT, token_id TEXT, pair_id TEXT)")
    conn.execute("CREATE TABLE quotes (token_id TEXT, side TEXT)")
    conn.execute("INSERT INTO orders VALUES ('o0','s0','m','BUY','tok-x','p1')")
    conn.execute("INSERT INTO fills VALUES ('o0',5.0,0.6,100000.0)")
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="no UP/DOWN quote side"):
        gsr.sweep(db, None)


def test_sweep_survives_a_run_with_no_fills(tmp_path):
    db = _shadow(tmp_path, [])

    text = gsr.sweep(db, None)

    assert "nothing to sweep" in text

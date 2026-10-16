"""The #622 comparison report, on fixture stores.

The report decides which refusal policy ships, so the things that would make it
lie are what these tests pin: a metric that silently crosses run ids, a censored
order read as a zero-second fill, a NULL slippage field counted as a zero, and a
trend/chop label that comes out of the policy rather than the tape.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from core_brain.order_registry import SCHEMA as REGISTRY_SCHEMA
from scripts import book_tape_recorder as btr
from scripts import refusal_hold_report as rhr

SEC = 1000  # posted_ts / cancelled_ts are in milliseconds


def _store(tmp_path: Path, name: str = "arm.db") -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / name))
    conn.executescript(REGISTRY_SCHEMA)
    conn.commit()
    return conn


def _order(conn, oid, run_id, *, posted=100 * SEC, status="open",
           token="tok-up", cid="0xm", cancel_reason=None, cancelled_ts=None):
    conn.execute(
        "INSERT INTO orders (id, order_id, condition_id, token_id, side, price,"
        " original_size, status, posted_ts, last_polled_ts, cancel_reason,"
        " cancelled_ts, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (oid, oid, cid, token, "BUY", 0.60, 5.0, status, posted, posted,
         cancel_reason, cancelled_ts, run_id))


def _fill(conn, trade_id, oid, run_id, ts):
    conn.execute(
        "INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts,"
        " recorded_ts, run_id) VALUES (?,?,?,?,?,?,?)",
        (trade_id, oid, 5.0, 0.60, ts, ts, run_id))


def _close(conn, run_id, *, method="single_buy_exit", trigger_bid=None,
           fresh_bid=None, proceeds=None, shares=None):
    conn.execute(
        "INSERT INTO closes (ts, method, shares, proceeds, run_id, trigger_bid,"
        " fresh_bid) VALUES (?,?,?,?,?,?,?)",
        (1.0, method, shares, proceeds, run_id, trigger_bid, fresh_bid))


def _visit(conn, run_id, code, size=1.0):
    conn.execute(
        "INSERT INTO market_events (ts, condition_id, kind, reason, reason_code,"
        " size, run_id) VALUES (?,?,?,?,?,?,?)",
        (1.0, "0xm", "refusal_visit", "why", code, size, run_id))


def _book(tmp_path: Path, series: dict[str, list[float]]) -> Path:
    """A book tape where each market has a hand-made mid series."""
    db = tmp_path / "book.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(btr.SCHEMA)
    for cid, mids in series.items():
        for idx, mid in enumerate(mids):
            conn.execute(
                "INSERT INTO book_samples (ts, run_id, condition_id, mid_up)"
                " VALUES (?,?,?,?)", (float(idx), "tape", cid, mid))
    conn.commit()
    conn.close()
    return db


# --- run-id scoping ----------------------------------------------------------

def test_every_metric_is_scoped_by_run_id(tmp_path):
    conn = _store(tmp_path)
    # Run A: 2 posted, 1 cancelled. Run B (another arm): 10 posted, 9 cancelled.
    for i in range(2):
        _order(conn, f"a{i}", "run-a")
    for i in range(10):
        _order(conn, f"b{i}", "run-b")
    _order(conn, "a-done", "run-a", status="cancelled",
           cancel_reason="hold_drift", cancelled_ts=105 * SEC)
    for i in range(9):
        _order(conn, f"b-done{i}", "run-b", status="cancelled",
               cancel_reason="not_quoted", cancelled_ts=105 * SEC)
    conn.commit()
    conn.close()

    result = rhr.arm(tmp_path / "arm.db", "run-a")
    assert result["churn"]["posted"] == 3
    assert result["churn"]["cancelled"] == 1
    assert result["churn"]["reasons"] == {"hold_drift": 1}


# --- censoring ---------------------------------------------------------------

def test_an_order_that_never_filled_is_censored_not_dropped(tmp_path):
    conn = _store(tmp_path)
    _order(conn, "filled", "run-a", posted=100 * SEC)
    _fill(conn, "t1", "filled", "run-a", 110 * SEC)
    _order(conn, "never", "run-a", posted=100 * SEC, status="cancelled",
           cancel_reason="not_quoted", cancelled_ts=160 * SEC)
    conn.commit()
    conn.close()

    ttf = rhr.arm(tmp_path / "arm.db", "run-a")["time_to_fill"]
    assert ttf["observations"] == 2, "a censored order is still an observation"
    assert ttf["filled"] == 1
    assert ttf["censored"] == 1


def test_the_km_median_counts_orders_that_never_filled(tmp_path):
    # Three orders fill at 2s, three at 10s, and four never fill. The
    # filled-only median is 6s -- the arm looks faster than it is, because the
    # orders that never filled simply vanish. Kaplan-Meier keeps them as
    # observations and reports 10s.
    conn = _store(tmp_path)
    for i in range(3):
        _order(conn, f"f{i}", "run-a", posted=100 * SEC)
        _fill(conn, f"t{i}", f"f{i}", "run-a", 102 * SEC)
    for i in range(3):
        _order(conn, f"g{i}", "run-a", posted=100 * SEC)
        _fill(conn, f"u{i}", f"g{i}", "run-a", 110 * SEC)
    for i in range(4):
        _order(conn, f"n{i}", "run-a", posted=100 * SEC, status="cancelled",
               cancel_reason="not_quoted", cancelled_ts=400 * SEC)
    conn.commit()
    conn.close()

    ttf = rhr.arm(tmp_path / "arm.db", "run-a")["time_to_fill"]
    assert ttf["filled"] == 6
    assert ttf["censored"] == 4
    assert ttf["median_filled_only_sec"] == 6.0
    assert ttf["km_median_sec"] == 10.0, (
        "the censored median must account for the orders that never filled")


def test_a_median_that_is_never_reached_is_reported_as_None(tmp_path):
    # Every order is still unfilled when observation ends. Fewer than half ever
    # filled, so the median genuinely does not exist yet -- "not reached" is
    # the answer, and a number here would be an invention.
    conn = _store(tmp_path)
    for i in range(4):
        _order(conn, f"n{i}", "run-a", posted=100 * SEC, status="cancelled",
               cancel_reason="not_quoted", cancelled_ts=400 * SEC)
    conn.commit()
    conn.close()

    ttf = rhr.arm(tmp_path / "arm.db", "run-a")["time_to_fill"]
    assert ttf["censored"] == 4
    assert ttf["km_median_sec"] is None


def test_a_censor_time_uses_the_cancel_clock_when_there_is_one(tmp_path):
    # With the #622 T4 column, an unfilled order stops being observed when it
    # was cancelled. Without it the fallback is the run's last posted order, so
    # the difference is directly visible in the censor time.
    conn = _store(tmp_path)
    _order(conn, "fast-cancel", "run-a", posted=100 * SEC, status="cancelled",
           cancel_reason="not_quoted", cancelled_ts=102 * SEC)
    _order(conn, "slow-cancel", "run-a", posted=100 * SEC, status="cancelled",
           cancel_reason="not_quoted", cancelled_ts=500 * SEC)
    # A later post, which sets the fallback horizon at 400s. Without a cancel
    # clock the slow cancel would be censored at 400 - 100 = 300s instead of the
    # 400s it actually waited, so the two rules are distinguishable.
    _order(conn, "later", "run-a", posted=400 * SEC)
    conn.commit()
    conn.close()

    ttf = rhr.arm(tmp_path / "arm.db", "run-a")["time_to_fill"]
    assert ttf["censored"] == 3, "all three orders are unfilled"
    assert ttf["store_horizon_sec"] == 400.0
    assert ttf["max_censor_sec"] == 400.0, (
        "the slow cancel observed 400s from its own cancel clock, not the "
        "300s the store horizon would have given it")


def test_the_censor_falls_back_to_the_store_horizon_without_the_clock(tmp_path):
    # A store written before #622 T4 has no cancelled_ts; the censoring must
    # degrade to "observed until the run's last posted order" rather than
    # erroring or censoring at zero.
    conn = _store(tmp_path)
    conn.execute(
        "INSERT INTO orders (id, order_id, condition_id, token_id, side, "
        "price, original_size, status, posted_ts, last_polled_ts, run_id) "
        "VALUES ('o1','o1','0xm','tok-up','BUY',0.6,5.0,'cancelled',? , ?, ?)",
        (100 * SEC, 100 * SEC, "run-a"))
    conn.execute(
        "INSERT INTO orders (id, order_id, condition_id, token_id, side, "
        "price, original_size, status, posted_ts, last_polled_ts, run_id) "
        "VALUES ('o2','o2','0xm','tok-up','BUY',0.6,5.0,'open',? , ?, ?)",
        (400 * SEC, 400 * SEC, "run-a"))
    conn.commit()
    conn.close()

    ttf = rhr.arm(tmp_path / "arm.db", "run-a")["time_to_fill"]
    assert ttf["censored"] == 2
    assert ttf["max_censor_sec"] == 300.0


# --- churn -------------------------------------------------------------------

def test_drift_cancels_are_counted_under_their_own_reason(tmp_path):
    # Option C must be countable apart from the churn it claims to prevent.
    conn = _store(tmp_path)
    _order(conn, "o1", "run-a", status="cancelled",
           cancel_reason="hold_drift", cancelled_ts=110 * SEC)
    _order(conn, "o2", "run-a", status="cancelled",
           cancel_reason="not_quoted", cancelled_ts=130 * SEC)
    _order(conn, "o3", "run-a")
    conn.commit()
    conn.close()

    churn = rhr.arm(tmp_path / "arm.db", "run-a")["churn"]
    assert churn["reasons"]["hold_drift"] == 1
    assert churn["reasons"]["not_quoted"] == 1
    assert churn["median_cancelled_life_sec"] == 20.0


def test_a_cancel_without_a_clock_is_reported_as_unmeasured(tmp_path):
    # Stores written before #622 T4 have no cancelled_ts. The lifetime must be
    # reported as missing, never as zero.
    conn = _store(tmp_path)
    _order(conn, "o1", "run-a", status="cancelled", cancel_reason="not_quoted",
           cancelled_ts=110 * SEC)
    _order(conn, "o2", "run-a", status="cancelled", cancel_reason="not_quoted")
    conn.commit()
    conn.close()

    churn = rhr.arm(tmp_path / "arm.db", "run-a")["churn"]
    assert churn["lives_measured"] == 1
    assert churn["lives_unmeasured"] == 1
    assert churn["median_cancelled_life_sec"] == 10.0


def test_a_store_without_the_cancel_clock_reports_its_cancels_as_unmeasured(
        tmp_path):
    # A store written before #622 T4 has no cancel clock. Its cancels are real,
    # so "0 measured / 0 unmeasured" would read as "no cancels" -- a different
    # and false claim.
    legacy = REGISTRY_SCHEMA.replace("    cancelled_ts INTEGER,\n", "")
    assert "cancelled_ts" not in legacy, "the DDL declaration moved"
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.executescript(legacy)
    for i in range(2):
        conn.execute(
            "INSERT INTO orders (id, order_id, condition_id, token_id, side,"
            " price, original_size, status, posted_ts, last_polled_ts,"
            " cancel_reason, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"o{i}", f"o{i}", "0xm", "tok-up", "BUY", 0.6, 5.0, "cancelled",
             100 * SEC, 100 * SEC, "not_quoted", "run-a"))
    conn.commit()
    conn.close()

    churn = rhr.arm(tmp_path / "legacy.db", "run-a")["churn"]
    assert churn["cancelled"] == 2
    assert churn["lives_measured"] == 0
    assert churn["lives_unmeasured"] == 2
    assert churn["median_cancelled_life_sec"] is None


def test_a_replace_is_a_cancel_with_a_later_post_on_the_same_token(tmp_path):
    conn = _store(tmp_path)
    # o1 cancelled, then o2 posted on the same token => one replacement.
    _order(conn, "o1", "run-a", posted=100 * SEC, status="cancelled",
           token="tok-up", cid="0xa", cancel_reason="not_quoted")
    _order(conn, "o2", "run-a", posted=130 * SEC, token="tok-up", cid="0xa")
    # o3 cancelled with no later post on its token => not a replacement.
    _order(conn, "o3", "run-a", posted=100 * SEC, status="cancelled",
           token="tok-dn", cid="0xa", cancel_reason="not_quoted")
    conn.commit()
    conn.close()

    churn = rhr.arm(tmp_path / "arm.db", "run-a")["churn"]
    assert churn["replacements"] == 1
    assert churn["markets_with_replacement"] == 1


# --- stop-outs and slippage --------------------------------------------------

def test_stop_out_rate_divides_stop_closes_by_filled_legs(tmp_path):
    conn = _store(tmp_path)
    for i in range(4):
        _order(conn, f"o{i}", "run-a")
        _fill(conn, f"t{i}", f"o{i}", "run-a", 110 * SEC)
    _close(conn, "run-a", method="single_buy_exit")
    _close(conn, "run-a", method="shadow_merge")
    conn.commit()
    conn.close()

    stops = rhr.arm(tmp_path / "arm.db", "run-a")["stop_outs"]
    assert stops["filled_legs"] == 4
    assert stops["stop_closes"] == 1
    assert stops["stop_out_rate"] == 0.25
    assert stops["methods"] == {"single_buy_exit": 1, "shadow_merge": 1}


def test_null_bid_fields_reduce_coverage_rather_than_counting_as_zero(tmp_path):
    conn = _store(tmp_path)
    # One close with both bids, one with neither. A zero-slippage row would
    # halve the median and invent a field the stop never recorded.
    _close(conn, "run-a", trigger_bid=0.40, fresh_bid=0.38,
           proceeds=3.6, shares=10.0)
    _close(conn, "run-a", trigger_bid=None, fresh_bid=None)
    conn.commit()
    conn.close()

    slip = rhr.arm(tmp_path / "arm.db", "run-a")["stop_slippage"]
    assert slip["closes"] == 2
    assert slip["with_trigger_bid"] == 1
    assert slip["with_fresh_bid"] == 1
    assert slip["trigger_minus_fresh"]["n"] == 1
    assert slip["trigger_minus_fresh"]["median"] == 0.40 - 0.38
    # proceeds/shares = 0.36, so the realized exit was 4c worse than the trigger.
    assert slip["trigger_minus_realized"]["median"] == 0.40 - 0.36


def test_slippage_is_unmeasured_when_no_close_has_the_fields(tmp_path):
    conn = _store(tmp_path)
    _close(conn, "run-a", method="shadow_merge")
    conn.commit()
    conn.close()

    slip = rhr.arm(tmp_path / "arm.db", "run-a")["stop_slippage"]
    assert slip["trigger_minus_fresh"]["n"] == 0
    assert slip["trigger_minus_fresh"]["median"] is None


# --- trend versus chop -------------------------------------------------------

def test_a_one_way_series_is_trend_and_an_oscillating_one_is_chop(tmp_path):
    book = _book(tmp_path, {
        "0xtrend": [0.40, 0.44, 0.48, 0.52, 0.56],
        "0xchop": [0.40, 0.50, 0.40, 0.50, 0.40],
    })
    conn = sqlite3.connect(str(book))
    conn.row_factory = sqlite3.Row
    labels = rhr.market_labels(conn, 0.5, "tape")
    conn.close()

    assert labels["0xtrend"]["label"] == "trend"
    assert labels["0xtrend"]["ratio"] == 1.0
    assert labels["0xchop"]["label"] == "chop"
    assert labels["0xchop"]["ratio"] < 0.5


def test_a_flat_series_is_chop_not_a_zero_division(tmp_path):
    book = _book(tmp_path, {"0xflat": [0.50, 0.50, 0.50]})
    conn = sqlite3.connect(str(book))
    conn.row_factory = sqlite3.Row
    labels = rhr.market_labels(conn, 0.5, "tape")
    conn.close()
    assert labels["0xflat"]["label"] == "chop"
    assert labels["0xflat"]["ratio"] == 0.0


def test_fill_rate_is_reported_per_label(tmp_path):
    book = _book(tmp_path, {
        "0xtrend": [0.40, 0.44, 0.48, 0.52],
        "0xchop": [0.40, 0.50, 0.40, 0.50],
    })
    conn = _store(tmp_path)
    _order(conn, "t1", "run-a", cid="0xtrend")
    _fill(conn, "ft1", "t1", "run-a", 110 * SEC)
    _order(conn, "c1", "run-a", cid="0xchop")
    conn.commit()
    conn.close()

    book_conn = sqlite3.connect(str(book))
    book_conn.row_factory = sqlite3.Row
    try:
        result = rhr.arm(tmp_path / "arm.db", "run-a", book_conn, 0.5)
    finally:
        book_conn.close()

    assert result["fill_rate_by_label"]["trend"]["fill_rate"] == 1.0
    assert result["fill_rate_by_label"]["chop"]["fill_rate"] == 0.0
    assert result["label_counts"] == {"trend": 1, "chop": 1}


def test_the_label_needs_a_tape_and_says_so_when_it_has_none(tmp_path):
    # Reporting a fill rate per label with no tape would be inventing the label.
    conn = _store(tmp_path)
    _order(conn, "o1", "run-a")
    conn.commit()
    conn.close()
    result = rhr.arm(tmp_path / "arm.db", "run-a")
    assert result["labels_available"] is False
    assert result["fill_rate_by_label"]["unlabelled"]["posted"] == 1


# --- run checks and failures -------------------------------------------------

def test_refusal_visits_are_the_denominator_from_the_store(tmp_path):
    conn = _store(tmp_path)
    _order(conn, "o1", "run-a")
    _visit(conn, "run-a", "refused_hold", size=1.0)
    _visit(conn, "run-a", "refused_hold", size=2.0)
    _visit(conn, "run-a", "refused_drift", size=1.0)
    _visit(conn, "run-b", "refused_hold")
    conn.commit()
    conn.close()

    run = rhr.arm(tmp_path / "arm.db", "run-a")["run"]
    assert run["refusal_visits_total"] == 3
    assert run["refusal_visits"]["refused_hold"] == 2
    assert run["refusal_visits"]["refused_drift"] == 1


def test_a_missing_store_is_reported_unavailable(tmp_path):
    result = rhr.arm(tmp_path / "nope.db", "run-a")
    assert result["status"] == "unavailable"
    assert "no such store" in result["error"]


def test_a_report_never_creates_or_writes_the_store(tmp_path):
    # The whole script is read-only: a missing file stays missing.
    target = tmp_path / "absent.db"
    rhr.arm(target, "run-a")
    assert not target.exists()


def test_the_script_puts_the_repo_root_on_the_path():
    # `python scripts/refusal_hold_report.py` puts `scripts/` on sys.path, not
    # the repo root. Without this insert the code-revision import failed and was
    # reported as `unknown` on a machine where the revision was readable --
    # "no git" and "import path broken" must not look the same.
    assert str(rhr.ROOT) in sys.path
    assert (rhr.ROOT / "core_brain" / "code_revision.py").exists()


def test_the_report_renders_every_required_metric_row(tmp_path):
    conn = _store(tmp_path)
    _order(conn, "o1", "run-a", status="cancelled",
           cancel_reason="hold_drift", cancelled_ts=110 * SEC)
    _visit(conn, "run-a", "refused_drift")
    conn.commit()
    conn.close()

    text = rhr.render(
        {"remove": rhr.arm(tmp_path / "arm.db", "run-a"),
         "drift": rhr.arm(tmp_path / "arm.db", "run-a")},
        0.5, book_used=False)
    for required in ("APPROXIMATELY CONCURRENT", "STOP-OUT RATE",
                     "TIME-TO-FILL", "STOP SLIPPAGE", "REFUSAL VISITS",
                     "median cancelled life", "replacements"):
        assert required in text, f"report is missing {required!r}"
    assert "NOT measured" in text, (
        "a missing tape must be stated, not silently reported as zeros")

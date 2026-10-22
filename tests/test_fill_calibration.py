"""Tests for `core_brain.fill_calibration` (issue #474).

The fixture numbers are hand-computed so every expected rate and gap is an exact
binary fraction (0.625 = 5/8, 0.375 = 3/8, 0.75, 0.5). Equality is asserted
directly -- never `pytest.approx` -- because a tolerance is the one thing that
could hide a wrong denominator, which is the bug this report must not have.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from core_brain.fill_calibration import (
    INSUFFICIENT,
    NO_DATA,
    OK,
    QUEUE_UNKNOWN,
    SIGN_RULE,
    UNREADABLE,
    OrderSample,
    build_report,
    compare,
    load_store,
    main,
    price_bucket,
    queue_bucket,
    render_text,
    summarize,
)
from core_brain.order_registry import (
    FillRecord,
    OrderRecord,
    OrderRegistry,
    QuoteRecord,
)

SHADOW_RUN = "shadow-A"
LIVE_RUN = "live-A"
OTHER_RUN = "live-other"
#: The run id on L1's fill. It is deliberately NOT `LIVE_RUN`: the run filter is
#: an order filter, so the order owns its fill whatever the fill says.
FILL_RUN = "live-B"
SIZE = 10.0


def _post(
    reg: OrderRegistry,
    local_id: str,
    price: float,
    *,
    run_id: str,
    status: str,
    queue_ahead: float | None = None,
    fills: tuple[tuple[str, float], ...] = (),
    fill_run_id: str | None = None,
    quote_rows: int = 1,
) -> None:
    """Seed one order with its quote row(s) and fills, through the real registry."""
    for n in range(quote_rows):
        reg.log_quote(QuoteRecord(
            ts=float(n + 1),
            condition_id="0xcope",
            market_slug="fake-market",
            token_id=f"tok-{local_id}",
            side="UP",
            price=price,
            size=SIZE,
            queue_ahead=queue_ahead,
            local_id=local_id,
            run_id=run_id,
        ))
    reg.create_order(OrderRecord(
        id=local_id,
        condition_id="0xcope",
        token_id=f"tok-{local_id}",
        side="BUY",
        price=price,
        original_size=SIZE,
        status=status,
        posted_ts=1_000,
        last_polled_ts=2_000,
        run_id=run_id,
    ))
    for trade_id, size in fills:
        reg.record_fill(FillRecord(
            trade_id=trade_id,
            order_uuid=local_id,
            size=size,
            price=price,
            venue_ts=1_001,
            recorded_ts=1_002,
            run_id=fill_run_id or run_id,
        ))


@pytest.fixture
def shadow_db(tmp_path):
    """Shadow store: four cohort rests plus one taker completion BUY (`C1`)."""
    path = tmp_path / "shadow.db"
    reg = OrderRegistry(db_path=path)
    _post(reg, "S1", 0.42, run_id=SHADOW_RUN, status="filled",
          queue_ahead=0.0, fills=(("t-s1", 10.0),))
    _post(reg, "S2", 0.42, run_id=SHADOW_RUN, status="partial",
          queue_ahead=20.0, fills=(("t-s2a", 2.0), ("t-s2b", 3.0)))
    _post(reg, "S3", 0.57, run_id=SHADOW_RUN, status="filled",
          queue_ahead=100.0, fills=(("t-s3", 10.0),))
    _post(reg, "S4", 0.57, run_id=SHADOW_RUN, status="cancelled", queue_ahead=200.0)
    # C1 is the taker completion buy: it is created and filled with NO quote
    # row, exactly as `shadow_exec` writes one.
    reg.create_order(OrderRecord(
        id="C1",
        condition_id="0xcope",
        token_id="tok-C1",
        side="BUY",
        price=0.50,
        original_size=SIZE,
        status="filled",
        posted_ts=1_000,
        last_polled_ts=2_000,
        pair_id="pair-1",
        run_id=SHADOW_RUN,
    ))
    reg.record_fill(FillRecord(
        trade_id="t-c1", order_uuid="C1", size=SIZE, price=0.50,
        venue_ts=1_001, recorded_ts=1_002, run_id=SHADOW_RUN,
    ))
    return path


@pytest.fixture
def live_db(tmp_path):
    """Live registry: four cohort rests (L1 has a duplicate quote row) plus L5."""
    path = tmp_path / "live.db"
    reg = OrderRegistry(db_path=path)
    _post(reg, "L1", 0.42, run_id=LIVE_RUN, status="filled",
          fills=(("t-l1", 10.0),), fill_run_id=FILL_RUN, quote_rows=2)
    _post(reg, "L2", 0.42, run_id=LIVE_RUN, status="cancelled")
    _post(reg, "L3", 0.57, run_id=LIVE_RUN, status="open")
    _post(reg, "L4", 0.57, run_id=LIVE_RUN, status="open")
    _post(reg, "L5", 0.42, run_id=OTHER_RUN, status="open")
    return path


@pytest.fixture
def shadow_load(shadow_db):
    return load_store(shadow_db, run_id=SHADOW_RUN, label="shadow")


@pytest.fixture
def live_load(live_db):
    return load_store(live_db, run_id=LIVE_RUN, label="live")


def _sample(load, local_id: str) -> OrderSample:
    matches = [s for s in load.samples if s.local_id == local_id]
    assert len(matches) == 1, f"{local_id} appears {len(matches)} times in {load.label}"
    return matches[0]


def _by_bucket(rows):
    return {row.bucket: row for row in rows}


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _readonly_connect(path):
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


# --- T1: read-only loader -----------------------------------------------------

def test_cohort_excludes_completion_buy(shadow_load):
    assert shadow_load.status == OK
    ids = [s.local_id for s in shadow_load.samples]
    assert ids == ["S1", "S2", "S3", "S4"]
    assert "C1" not in ids


def test_partial_fills_are_summed(shadow_load):
    s2 = _sample(shadow_load, "S2")
    assert s2.status == "partial"
    assert s2.filled_shares == 5.0
    assert s2.has_fill is True


def test_cancelled_unfilled_order_stays_in(shadow_load):
    s4 = _sample(shadow_load, "S4")
    assert s4.status == "cancelled"
    assert s4.filled_shares == 0.0
    assert s4.has_fill is False
    assert len(shadow_load.samples) == 4


def test_duplicate_quote_counts_once(live_db, live_load):
    assert live_load.status == OK
    assert len(live_load.samples) == 4
    assert _sample(live_load, "L1").filled_shares == 10.0
    # The duplicate really is in the file: attribution stamped the whole order
    # fill onto BOTH quote rows, so summing `quotes.filled` would read 20.
    conn = _readonly_connect(live_db)
    try:
        rows = conn.execute(
            "SELECT filled FROM quotes WHERE local_id = 'L1'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    assert sum(float(r[0]) for r in rows) == 20.0


def test_cross_run_fill_counts_for_owning_order(live_db, live_load):
    conn = _readonly_connect(live_db)
    try:
        fill_run = conn.execute(
            "SELECT run_id FROM fills WHERE trade_id = 't-l1'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert fill_run == FILL_RUN
    l1 = _sample(live_load, "L1")
    assert l1.filled_shares == 10.0
    assert l1.has_fill is True


def test_run_filter_excludes_other_runs(live_db):
    filtered = load_store(live_db, run_id=LIVE_RUN, label="live")
    unfiltered = load_store(live_db, label="live")
    assert len(filtered.samples) == 4
    assert len(unfiltered.samples) == 5
    assert "L5" in [s.local_id for s in unfiltered.samples]
    assert "L5" not in [s.local_id for s in filtered.samples]


def test_missing_queue_is_none_not_zero(live_load):
    assert [s.local_id for s in live_load.samples] == ["L1", "L2", "L3", "L4"]
    assert all(s.queue_ahead is None for s in live_load.samples)


def test_overfill_is_capped_and_flagged(tmp_path):
    path = tmp_path / "overfill.db"
    reg = OrderRegistry(db_path=path)
    _post(reg, "O1", 0.42, run_id=SHADOW_RUN, status="filled",
          queue_ahead=0.0, fills=(("t-o1", 12.0),))
    load = load_store(path, label="shadow")
    assert load.status == OK
    sample = _sample(load, "O1")
    assert sample.filled_shares == 10.0
    assert sample.overfilled is True
    assert sample.has_fill is True


def test_inputs_are_not_modified(shadow_db, live_db):
    before = (_sha256(shadow_db), _sha256(live_db))
    shadow = load_store(shadow_db, run_id=SHADOW_RUN, label="shadow")
    live = load_store(live_db, run_id=LIVE_RUN, label="live")
    report = build_report(shadow, live, min_orders=1)
    render_text(report)
    report.to_dict()
    assert main(["--shadow-db", str(shadow_db), "--live-db", str(live_db),
                 "--min-orders", "1"]) == 0
    assert (_sha256(shadow_db), _sha256(live_db)) == before


def test_missing_path_is_no_data(tmp_path):
    missing = tmp_path / "not-there.db"
    load = load_store(missing, label="live")
    assert load.status == NO_DATA
    assert load.samples == ()
    assert load.detail
    assert not missing.exists()


def test_missing_quotes_table_is_unreadable(tmp_path):
    path = tmp_path / "no-quotes.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, side TEXT)")
    conn.execute("CREATE TABLE fills (trade_id TEXT PRIMARY KEY, order_uuid TEXT)")
    conn.commit()
    conn.close()
    load = load_store(path, label="shadow")
    assert load.status == UNREADABLE
    assert "quotes" in load.detail


def test_foreign_schema_is_unreadable(tmp_path):
    path = tmp_path / "stats_shadow-01.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL)"
    )
    conn.commit()
    conn.close()
    load = load_store(path, label="shadow")
    assert load.status == UNREADABLE
    assert load.detail
    assert load.samples == ()


def _corrupt(path, statement: str, value) -> None:
    """Edit one value in a test-owned store; the module itself never writes."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(statement, (value,))
        conn.commit()
    finally:
        conn.close()


def test_hash_in_path_opens_the_intended_file(tmp_path):
    """A raw `file:` URI is cut at `#`: the old code opened -- and created -- `a`."""
    path = tmp_path / "a#b.db"
    reg = OrderRegistry(db_path=path)
    _post(reg, "H1", 0.42, run_id=SHADOW_RUN, status="open", queue_ahead=0.0)
    load = load_store(path, run_id=SHADOW_RUN, label="shadow")
    assert load.status == OK
    assert [s.local_id for s in load.samples] == ["H1"]
    assert not (tmp_path / "a").exists()


def test_non_finite_original_size_is_unreadable(tmp_path):
    path = tmp_path / "inf.db"
    reg = OrderRegistry(db_path=path)
    _post(reg, "N1", 0.42, run_id=SHADOW_RUN, status="open")
    _corrupt(path, "UPDATE orders SET original_size = ?", float("inf"))
    load = load_store(path, run_id=SHADOW_RUN, label="shadow")
    assert load.status == UNREADABLE
    assert "not finite" in load.detail
    assert "N1" in load.detail
    assert load.samples == ()


def test_non_numeric_queue_ahead_is_unreadable(tmp_path):
    path = tmp_path / "bad-queue.db"
    reg = OrderRegistry(db_path=path)
    _post(reg, "Q1", 0.42, run_id=SHADOW_RUN, status="open", queue_ahead=5.0)
    _corrupt(path, "UPDATE quotes SET queue_ahead = ?", "n/a")
    load = load_store(path, run_id=SHADOW_RUN, label="shadow")
    assert load.status == UNREADABLE
    assert "Q1" in load.detail
    assert load.samples == ()


def test_json_never_emits_nan(tmp_path, live_db, capsys):
    """`inf / inf` is `nan`: the old path printed `NaN`, which is not valid JSON."""
    path = tmp_path / "inf-json.db"
    reg = OrderRegistry(db_path=path)
    _post(reg, "N1", 0.42, run_id=SHADOW_RUN, status="filled",
          queue_ahead=0.0, fills=(("t-n1", 5.0),))
    _corrupt(path, "UPDATE orders SET original_size = ?", float("inf"))
    _corrupt(path, "UPDATE fills SET size = ?", float("inf"))
    rc = main(["--shadow-db", str(path), "--live-db", str(live_db),
               "--live-run-id", LIVE_RUN, "--min-orders", "1", "--json"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "NaN" not in out
    payload = json.loads(out)
    assert payload["shadow"]["status"] == UNREADABLE
    assert payload["overall"]["share_gap"] is None


# --- T2: buckets and signed gap math -----------------------------------------

def test_price_bucket_edges():
    assert price_bucket(0.40) == "0.40-0.50"
    assert price_bucket(0.3999) == "0.30-0.40"
    assert price_bucket(1.00) == "0.90-1.00"


def test_queue_bucket_edges():
    assert queue_bucket(0.0) == "0-1"
    assert queue_bucket(1.0) == "1-50"
    assert queue_bucket(400.0) == "400+"
    assert queue_bucket(None) == QUEUE_UNKNOWN == "unknown"


def test_overall_gap(shadow_load, live_load):
    overall, _, _ = compare(shadow_load.samples, live_load.samples, min_orders=1)
    assert overall.dimension == "overall"
    assert overall.shadow.orders == 4
    assert overall.live.orders == 4
    assert overall.shadow.share_fill_rate == 0.625
    assert overall.shadow.order_fill_rate == 0.75
    assert overall.live.share_fill_rate == 0.25
    assert overall.live.order_fill_rate == 0.25
    assert overall.order_gap == 0.5
    assert overall.share_gap == 0.375


def test_gap_sign_is_shadow_minus_live(shadow_load, live_load):
    swapped, _, _ = compare(live_load.samples, shadow_load.samples, min_orders=1)
    assert swapped.order_gap == -0.5
    assert swapped.share_gap == -0.375


def test_price_bucket_gaps(shadow_load, live_load):
    _, price_rows, _ = compare(shadow_load.samples, live_load.samples, min_orders=1)
    rows = _by_bucket(price_rows)
    assert rows["0.40-0.50"].order_gap == 0.5
    assert rows["0.40-0.50"].share_gap == 0.25
    assert rows["0.50-0.60"].order_gap == 0.5
    assert rows["0.50-0.60"].share_gap == 0.5


def test_live_queue_rows_are_unknown(shadow_load, live_load):
    _, _, queue_rows = compare(shadow_load.samples, live_load.samples, min_orders=1)
    rows = _by_bucket(queue_rows)
    assert rows["0-1"].shadow.orders == 1
    assert rows["0-1"].live.orders == 0
    assert rows["unknown"].live.orders == 4
    assert rows["unknown"].shadow.orders == 0
    assert all(r.share_gap is None and r.order_gap is None for r in queue_rows)


def test_min_orders_blocks_thin_buckets(shadow_load, live_load):
    _, price_rows, _ = compare(shadow_load.samples, live_load.samples, min_orders=3)
    row = _by_bucket(price_rows)["0.40-0.50"]
    assert row.status == INSUFFICIENT
    assert row.share_gap is None
    assert row.order_gap is None
    assert row.shadow.orders == 2
    assert row.live.orders == 2
    assert row.shadow.posted_shares == 20.0
    assert row.live.posted_shares == 20.0


def test_zero_posted_shares_gives_none():
    empty = summarize([], "all")
    assert empty.orders == 0
    assert empty.share_fill_rate is None
    assert empty.order_fill_rate is None

    zero_posted = OrderSample(
        local_id="Z", price=0.42, posted_shares=0.0, queue_ahead=None,
        status="cancelled", filled_shares=0.0, overfilled=False,
    )
    one = summarize([zero_posted], "0.40-0.50")
    assert one.orders == 1
    assert one.share_fill_rate is None      # zero denominator is None, never 0.0
    assert one.order_fill_rate == 0.0       # a real zero from a real denominator


def test_bucket_gap_ok_status(shadow_load, live_load):
    overall, price_rows, _ = compare(shadow_load.samples, live_load.samples, min_orders=1)
    assert overall.status == OK
    assert len(price_rows) == 2
    assert {row.status for row in price_rows} == {OK}


# --- T3: rendering and CLI ----------------------------------------------------

def test_main_json(shadow_db, live_db, capsys):
    rc = main([
        "--shadow-db", str(shadow_db),
        "--live-db", str(live_db),
        "--shadow-run-id", SHADOW_RUN,
        "--live-run-id", LIVE_RUN,
        "--min-orders", "1",
        "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["overall"]["order_gap"] == 0.5
    assert payload["overall"]["share_gap"] == 0.375
    assert payload["sign_rule"] == SIGN_RULE


def test_main_text(shadow_db, live_db, capsys):
    rc = main([
        "--shadow-db", str(shadow_db),
        "--live-db", str(live_db),
        "--shadow-run-id", SHADOW_RUN,
        "--live-run-id", LIVE_RUN,
        "--min-orders", "1",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "gap" in out
    assert "positive gap = shadow fills more than live" in out
    assert "unknown" in out
    assert "--" in out


def test_main_missing_live_db(shadow_db, tmp_path, capsys):
    missing = tmp_path / "never-created.db"
    rc = main([
        "--shadow-db", str(shadow_db),
        "--live-db", str(missing),
        "--shadow-run-id", SHADOW_RUN,
    ])
    out = capsys.readouterr().out
    assert rc == 1
    assert NO_DATA in out
    assert not missing.exists()

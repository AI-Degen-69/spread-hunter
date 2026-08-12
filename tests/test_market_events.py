"""Operator-facing market event telemetry tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_reason_codes_are_stable_and_specific(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "events.db"))
    from strategy import store

    assert store.reason_code("$120 naked >= $120 budget -- not adding") == "NAKED_CAP"
    assert store.reason_code("hedge token DOWN not tradeable (one-sided book)") == "ONE_SIDED_BOOK"
    assert store.reason_code("pair 0.520+0.480=$1.0000 cap") == "PAIR_COST"
    assert store.reason_code("fleet HALTED on pooled markout") == "FLEET_HALTED"
    # `hard_block` wraps the hedge leg's own rejection in its prose, so a
    # settled hedge carries BOTH "not tradeable" and "settled book". The code
    # must name the binding cause -- the market has already decided -- not the
    # wrapper, which would send the operator hunting a missing side on a book
    # that has two.
    assert store.reason_code(
        "hedge token UP not tradeable (settled book 0.999/0.001) -- "
        "a fill here could not be closed") == "PRICE_BAND"
    assert store.reason_code("0.850 outside band 0.30-0.70") == "PRICE_BAND"


def test_market_event_is_durable_and_reason_code_is_stored(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "events.db"))
    from strategy import store

    store.log_event(condition_id="c", market_slug="m", kind="BLOCKED",
                    reason="book too thin 12sh < 50sh")
    with store.db() as c:
        row = c.execute(
            "SELECT kind, reason, reason_code FROM market_events"
        ).fetchone()
    assert row == ("BLOCKED", "book too thin 12sh < 50sh", "THIN_BOOK")


def test_decision_schema_migrates_reason_code(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "events.db"))
    from strategy import store

    store.log_decision(condition_id="c", market_slug="m", action="BLOCKED",
                       reason="pair cap", reason_code="PAIR_COST")
    store.flush_decision(force=True)
    with store.db() as c:
        row = c.execute(
            "SELECT action, reason_code, count FROM decisions"
        ).fetchone()
    assert row == ("BLOCKED", "PAIR_COST", 1)


def test_float_mark_roundtrip_thinning_and_prune(monkeypatch, tmp_path):
    """float_marks: one fleet-wide mark per sweep survives a write/read
    round-trip oldest-first; sub-minute marks are thinned out of the history
    (one point per 60s minimum); and the retention prune runs on the write
    path, so rows older than `prune_before` never come back."""
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "marks.db"))
    from strategy import store

    # One mark per minute: every row lands on the thinning boundary.
    for i, ts in enumerate([1000.0 + 60.0 * i for i in range(5)]):
        store.log_float_mark(ts, unrealized_usd=float(i),
                             committed_open_usd=10.0 + i, naked_usd=1.0 + i)
    # A sub-minute mark is written but thinned out of the read series.
    store.log_float_mark(1030.0, 99.0, 199.0, 29.0)

    hist = store.float_history()
    assert [h["ts"] for h in hist] == [1000.0, 1060.0, 1120.0, 1180.0, 1240.0]
    assert all(set(h) == {"ts", "unrealized_usd", "committed_open_usd",
                          "naked_usd"} for h in hist)
    assert hist[2]["unrealized_usd"] == 2.0

    # Prune on the write path: older rows are deleted before the new one.
    store.log_float_mark(5000.0, 1.0, 2.0, 3.0, prune_before=1100.0)
    hist = store.float_history()
    assert [h["ts"] for h in hist] == [1120.0, 1180.0, 1240.0, 5000.0]

    # The read is bounded: at most `max_points`, newest kept (thinning
    # disabled here so the cap is what binds). Bulk-inserted in one
    # transaction -- the writer's commit-per-row cost is not what is under
    # test here.
    with store.db() as c:
        c.executemany(
            "INSERT INTO float_marks (ts, unrealized_usd, "
            "committed_open_usd, naked_usd) VALUES (?,?,?,?)",
            [(6000.0 + i, 0.0, 0.0, 0.0) for i in range(3000)])
    hist = store.float_history(max_points=500, min_spacing_sec=0.0)
    assert len(hist) == 500
    assert hist[0]["ts"] == 8500.0 and hist[-1]["ts"] == 8999.0


def test_float_history_sparse_timestamps_not_excluded(monkeypatch, tmp_path):
    """Sparse marks (gaps far larger than min_spacing_sec) must not be
    excluded by the read window before thinning: the thinning loop widens
    its window until it holds `max_points` retained marks or reaches the
    oldest mark (coderabbit: the min_spacing-derived read window dropped
    sparse history -- marks at 0/3600/7200/10800 with max_points=3 and
    min_spacing=60 must keep the newest three, not just the last one)."""
    monkeypatch.setenv("HUNTER_DB", str(tmp_path / "marks_sparse.db"))
    from strategy import store

    for ts in (0.0, 3600.0, 7200.0, 10800.0):
        store.log_float_mark(ts, 1.0, 2.0, 3.0)

    hist = store.float_history(max_points=3, min_spacing_sec=60.0)
    assert [h["ts"] for h in hist] == [3600.0, 7200.0, 10800.0]

    # Every mark farther apart than the minimum spacing survives thinning;
    # the cap (newest `max_points`) is what binds on a sparse table.
    assert len(hist) == 3
    assert all(h["unrealized_usd"] == 1.0 for h in hist)


def test_float_history_read_does_not_create_db(monkeypatch, tmp_path):
    """float_history on a cold DB reads as an empty series and does NOT create
    the database file -- a dashboard poll must never materialise an empty
    hunter.db just by being looked at."""
    db_path = tmp_path / "cold.db"
    monkeypatch.setenv("HUNTER_DB", str(db_path))
    from strategy import store

    assert store.float_history() == []
    assert not db_path.exists()

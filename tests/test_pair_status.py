"""Pair status is owned by core_brain, the dashboard only adapts it.

`pairStatus` lived in dashboard/static/app.js with 5+ call sites computing
paired/partial/unpaired from two share counts. This pins the single rule the
server now shapes into each KPI market (`pair_status`) so the JS variants can
read instead of recompute. Fails without core_brain/pair_status.py.
"""
import pytest

from core_brain.pair_status import PAIR_STATUS, pair_status


@pytest.fixture
def temp_db(tmp_path):
    """A live registry database with the real schema, empty of orders."""
    import sqlite3
    from core_brain.order_registry import SCHEMA
    db_file = tmp_path / "live.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


def test_paired_when_equal_and_both_held():
    assert pair_status(10, 10) == "paired"
    assert pair_status(23, 23) == "paired"


def test_partial_when_both_held_but_unequal():
    assert pair_status(10, 6) == "partial"
    assert pair_status(6, 10) == "partial"


def test_unpaired_when_one_side_empty():
    assert pair_status(10, 0) == "unpaired"
    assert pair_status(0, 10) == "unpaired"
    assert pair_status(0, 0) == "unpaired"


def test_unpaired_on_missing_or_unmeasurable():
    assert pair_status(None, None) == "unpaired"
    assert pair_status(None, 10) == "unpaired"
    assert pair_status("10", "10") == "paired"
    assert pair_status(float("nan"), 10) == "unpaired"


def test_tones_match_the_open_positions_table():
    assert PAIR_STATUS["paired"]["tone"] == "good"
    assert PAIR_STATUS["partial"]["tone"] == "warn"
    assert PAIR_STATUS["unpaired"]["tone"] == "alert"


def test_status_meta_is_the_single_owner_of_label_and_tone():
    from core_brain.pair_status import status_meta
    assert status_meta("paired") == {"label": "Paired", "tone": "good"}
    assert status_meta("partial") == {"label": "Partial", "tone": "warn"}
    assert status_meta("unpaired") == {"label": "Unpaired", "tone": "alert"}


def test_status_meta_falls_back_to_unpaired_for_unknown():
    from core_brain.pair_status import status_meta
    assert status_meta("bogus") == {"label": "Unpaired", "tone": "alert"}
    assert status_meta(None) == {"label": "Unpaired", "tone": "alert"}


def test_describe_carries_word_plus_label_and_tone():
    from core_brain.pair_status import describe
    assert describe(10, 10) == {"status": "paired", "label": "Paired", "tone": "good"}
    assert describe(10, 6) == {"status": "partial", "label": "Partial", "tone": "warn"}
    assert describe(10, 0) == {"status": "unpaired", "label": "Unpaired", "tone": "alert"}


def test_kpi_shapes_label_and_tone_beside_the_status_word(temp_db, monkeypatch):
    from core_brain import kpi as kpi_mod
    from core_brain.kpi import report
    from core_brain.order_registry import OrderRecord, FillRecord, OrderRegistry
    import time
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", temp_db.parent)
    reg = OrderRegistry(temp_db)
    now = int(time.time())
    for uid, token in (("o-up", "tok-a-up"), ("o-dn", "tok-b-dn")):
        reg.create_order(OrderRecord(
            id=uid, condition_id="0xpair", token_id=token, side="BUY", price=0.48,
            original_size=10.0, status="filled", posted_ts=now, last_polled_ts=now,
            order_id=f"venue-{uid}", pair_id="pair-1", run_id="run-pair",
        ))
        reg.record_fill(FillRecord(trade_id=f"trade-{uid}", order_uuid=uid, size=10.0,
                                price=0.48, venue_ts=now * 1000, run_id="run-pair"))
    m = report(db_path=str(temp_db), run_id="all")["by_market"]["0xpair"]
    assert m["pair_status"] == "paired"
    assert m["pair_status_label"] == "Paired"
    assert m["pair_status_tone"] == "good"

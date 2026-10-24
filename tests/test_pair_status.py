"""Pair status is owned by core_brain, the dashboard only adapts it.

`pairStatus` lived in dashboard/static/app.js with 5+ call sites computing
paired/partial/unpaired from two share counts. This pins the single rule the
server now shapes into each KPI market (`pair_status`) so the JS variants can
read instead of recompute. Fails without core_brain/pair_status.py.
"""
from core_brain.pair_status import PAIR_STATUS, pair_status


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

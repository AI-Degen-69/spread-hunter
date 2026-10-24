"""Book-math interface tests (#806).

`core_brain.book_math` owns every number the Single buy path reads off a
book: touch prices, depth at a limit, and the venue tick grid. One pure
interface, one place to test — the saver calls it, never reimplements it.
"""
from __future__ import annotations

import pytest

from core_brain.book_math import (
    ask_depth_at_or_below,
    best_ask,
    best_bid,
    bid_depth,
    book_levels,
    depth_at_or_above,
    floor_to_tick,
    tick_size,
)


class FakeLevels:
    def __init__(self, bids=None, asks=None, tick_size=None):
        self.bids = bids or []
        self.asks = asks or []
        self.tick_size = tick_size


def _book():
    return {
        "bids": [{"price": "0.50", "size": "10"}, {"price": "0.48", "size": "20"}],
        "asks": [{"price": "0.52", "size": "5"}, {"price": "0.55", "size": "15"}],
        "tick_size": "0.01",
    }


def test_levels_accept_dict_and_object_shapes():
    book = _book()
    assert book_levels(book, "bids") == [(0.50, 10.0), (0.48, 20.0)]
    obj = FakeLevels(
        bids=[{"price": "0.50", "size": "10"}],
        asks=[{"price": "0.52", "size": "5"}],
    )
    assert book_levels(obj, "asks") == [(0.52, 5.0)]


def test_levels_skip_broken_rows():
    book = {
        "bids": [
            {"price": None, "size": "10"},
            {"price": "oops", "size": "10"},
            {"price": "0.50", "size": "10"},
        ],
        "asks": [],
    }
    assert book_levels(book, "bids") == [(0.50, 10.0)]


def test_touch_prices():
    book = _book()
    assert best_bid(book) == 0.50
    assert best_ask(book) == 0.52


def test_touch_prices_empty_book():
    assert best_bid({"bids": [], "asks": []}) is None
    assert best_ask({"bids": [], "asks": []}) is None


def test_depth_sums():
    assert bid_depth(_book()) == 30.0
    assert depth_at_or_above(_book(), 0.49) == 10.0
    assert depth_at_or_above(_book(), 0.48) == 30.0
    assert ask_depth_at_or_below(_book(), 0.52) == 5.0
    assert ask_depth_at_or_below(_book(), 0.55) == 20.0


@pytest.mark.parametrize("raw", [None, "0", "-0.01", "junk"])
def test_tick_size_falls_back_to_cent(raw):
    assert tick_size({"tick_size": raw}) == 0.01
    assert tick_size({}) == 0.01


def test_tick_size_reads_book():
    assert tick_size({"tick_size": "0.005"}) == 0.005


def test_floor_to_tick_keeps_aligned_prices():
    # 0.29 / 0.01 is 28.999... in binary floating point; a bare int()
    # would drop an aligned price a whole tick lower.
    assert floor_to_tick(0.29, 0.01) == pytest.approx(0.29)
    assert floor_to_tick(0.293, 0.01) == pytest.approx(0.29)
    assert floor_to_tick(0.53, 0.01) == pytest.approx(0.53)
    assert floor_to_tick(0.535, 0.01) == pytest.approx(0.53)
    assert floor_to_tick(0.07, 0.001) == pytest.approx(0.07)

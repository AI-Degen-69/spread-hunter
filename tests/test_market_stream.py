"""Live market stream engine (#648): stream-folded books, offline.

Every test is offline: frames are injected directly, the socket is stubbed,
and nothing here touches the venue or `data/orders.db`.
"""
from __future__ import annotations

import json
import statistics
import time

import pytest

from core_brain import markets
from core_brain.market_stream import (
    READ_LATENCY_BUDGET_SEC,
    BookNotReady,
    MarketBookCache,
)


def _lvl(price, size=10.0):
    return {"price": price, "size": size}


def _book_frame(token, bids, asks):
    return {"event_type": "book", "asset_id": token, "bids": bids, "asks": asks,
            "timestamp": "1782753357257"}


def _change(token, side, price, size, **rest):
    return {"asset_id": token, "price": price, "size": size, "side": side,
            **rest}


def _change_frame(*changes):
    return {"event_type": "price_change", "price_changes": list(changes),
            "timestamp": "1782753357258"}


def _loaded(*pairs):
    """A cache holding a snapshot for each (token, bids, asks)."""
    cache = MarketBookCache()
    for token, bids, asks in pairs:
        cache.apply_frame(_book_frame(token, bids, asks))
    return cache


# ── T1 — book cache and frame folding ──────────────────────────────────────


def test_book_snapshot_matches_parse_book_shape():
    """A stream book and a REST book are the same object, key for key."""
    cache = _loaded(("tok-u", [_lvl(0.46), _lvl(0.47)], [_lvl(0.50, 3.0)]))

    book = cache.book("tok-u")
    assert set(book) == set(markets.parse_book({"bids": [], "asks": []}, "tok"))
    assert book["token_id"] == "tok-u"
    assert book["bids"] == {0.46: 10.0, 0.47: 10.0}
    assert book["asks"] == {0.50: 3.0}
    assert book["best_bid"] == 0.47
    assert book["best_ask"] == 0.50
    assert book["malformed"] == 0


def test_book_snapshot_replaces_both_sides_never_merges():
    """A second snapshot is the whole book, not a patch on the first."""
    cache = _loaded(("tok-u", [_lvl(0.46), _lvl(0.47)], [_lvl(0.50)]))
    cache.apply_frame(_book_frame("tok-u", [_lvl(0.44)], [_lvl(0.55)]))

    book = cache.book("tok-u")
    assert book["bids"] == {0.44: 10.0}
    assert book["asks"] == {0.55: 10.0}
    assert book["best_bid"] == 0.44 and book["best_ask"] == 0.55


def test_price_change_buy_and_sell_update_the_right_side():
    cache = _loaded(("tok-u", [_lvl(0.46)], [_lvl(0.50)]))

    cache.apply_frame(_change_frame(_change("tok-u", "BUY", "0.48", "12"),
                                    _change("tok-u", "SELL", "0.51", "8")))
    book = cache.book("tok-u")
    assert book["bids"] == {0.46: 10.0, 0.48: 12.0}
    assert book["asks"] == {0.50: 10.0, 0.51: 8.0}
    assert book["best_bid"] == 0.48 and book["best_ask"] == 0.50

    # A better ask replaces the top of the other side only.
    cache.apply_frame(_change_frame(_change("tok-u", "SELL", "0.49", "4")))
    book = cache.book("tok-u")
    assert book["best_ask"] == 0.49
    assert book["best_bid"] == 0.48


def test_zero_size_removes_a_level_and_moves_best_bid():
    cache = _loaded(("tok-u", [_lvl(0.46), _lvl(0.47)], [_lvl(0.50)]))

    cache.apply_frame(_change_frame(_change("tok-u", "BUY", "0.47", "0")))
    book = cache.book("tok-u")
    assert 0.47 not in book["bids"]
    assert book["best_bid"] == 0.46

    cache.apply_frame(_change_frame(_change("tok-u", "BUY", "0.46", "0")))
    book = cache.book("tok-u")
    assert book["bids"] == {}
    assert book["best_bid"] is None


def test_multi_entry_price_change_frame():
    cache = _loaded(("tok-u", [_lvl(0.46)], [_lvl(0.50)]),
                    ("tok-d", [_lvl(0.40)], [_lvl(0.60)]))

    changed = cache.apply_frame(_change_frame(
        _change("tok-u", "BUY", "0.47", "5"),
        _change("tok-d", "SELL", "0.59", "7"),
        _change("tok-u", "SELL", "0.49", "2"),
    ))
    assert sorted(changed) == ["tok-d", "tok-u"]
    assert cache.book("tok-u")["best_bid"] == 0.47
    assert cache.book("tok-d")["best_ask"] == 0.59


def test_delta_for_a_token_without_a_snapshot_is_ignored():
    """A delta cannot be folded into a book nobody has seen yet."""
    cache = MarketBookCache()
    assert cache.apply_frame(_change_frame(
        _change("tok-u", "BUY", "0.48", "5"))) == []
    with pytest.raises(BookNotReady):
        cache.book("tok-u")


def test_unknown_event_type_is_ignored():
    cache = _loaded(("tok-u", [_lvl(0.46)], [_lvl(0.50)]))

    for frame in ({"event_type": "last_trade_price", "asset_id": "tok-u",
                   "price": "0.49", "size": "3"},
                  {"event_type": "tick_size_change", "asset_id": "tok-u",
                   "old_tick_size": "0.01", "new_tick_size": "0.001"},
                  {"event_type": "best_bid_ask", "asset_id": "tok-u",
                   "bids": [_lvl(0.99)], "asks": [_lvl(0.99)]}):
        assert cache.apply_frame(frame) == []

    book = cache.book("tok-u")
    assert book["bids"] == {0.46: 10.0} and book["asks"] == {0.50: 10.0}


def test_corrupt_level_invalidates_that_token_only():
    cache = _loaded(("tok-u", [_lvl(0.46)], [_lvl(0.50)]),
                    ("tok-d", [_lvl(0.40)], [_lvl(0.60)]))

    # A delta level we cannot read leaves the book silently diverged from the
    # venue: that token drops (fail closed) and waits for the next snapshot.
    cache.apply_frame(_change_frame(_change("tok-u", "BUY", "abc", "5")))
    with pytest.raises(BookNotReady):
        cache.book("tok-u")
    assert cache.book("tok-d")["best_bid"] == 0.40

    # A snapshot's row garbage is skipped and counted instead: the streamed
    # book keeps the REST tolerance `parse_book` already defines.
    cache.apply_frame(_book_frame("tok-u", [_lvl(0.45), _lvl("abc")],
                                  [_lvl(0.52)]))
    book = cache.book("tok-u")
    assert book["bids"] == {0.45: 10.0}
    assert book["malformed"] == 1

    # A structurally broken snapshot (a side that is not a list) drops the
    # token rather than keeping half a book.
    cache.apply_frame(_book_frame("tok-u", {"0.45": 10.0}, [_lvl(0.52)]))
    with pytest.raises(BookNotReady):
        cache.book("tok-u")
    assert cache.book("tok-d")["best_bid"] == 0.40


def test_unknown_token_raises_book_not_ready():
    cache = _loaded(("tok-u", [_lvl(0.46)], [_lvl(0.50)]))

    with pytest.raises(BookNotReady):
        cache.book("tok-nowhere")
    with pytest.raises(BookNotReady):
        cache.book(None)

    cache.drop(["tok-u"])
    with pytest.raises(BookNotReady):
        cache.book("tok-u")

    cache.apply_frame(_book_frame("tok-u", [_lvl(0.46)], [_lvl(0.50)]))
    assert cache.book("tok-u")["best_bid"] == 0.46
    cache.clear_all()
    with pytest.raises(BookNotReady):
        cache.book("tok-u")


def test_read_latency_p50_is_under_a_millisecond():
    """The whole point of the cache: a read is a lookup, not a network call."""
    bids = [_lvl(f"0.{p:02d}", 5.0) for p in range(1, 100)]
    asks = [_lvl(f"0.{p:02d}", 5.0) for p in range(100, 200)]
    cache = _loaded(("tok-u", bids, asks))
    cache.book("tok-u")  # warm

    samples = []
    for _ in range(200):
        start = time.perf_counter()
        cache.book("tok-u")
        samples.append(time.perf_counter() - start)

    assert statistics.median(samples) < READ_LATENCY_BUDGET_SEC, (
        f"p50 read {statistics.median(samples) * 1e6:.0f}us")

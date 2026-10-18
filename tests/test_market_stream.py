"""Live market stream engine (#648): stream-folded books, offline.

Every test is offline: frames are injected directly, the socket is stubbed,
and nothing here touches the venue or `data/orders.db`.
"""
from __future__ import annotations

import json
import statistics
import sys
import threading
import time
import types
from types import SimpleNamespace

import pytest

from core_brain import markets
from core_brain import market_stream as ms
from core_brain.market_stream import (
    LIVE_WINDOW_SEC,
    PING_TEXT,
    READ_LATENCY_BUDGET_SEC,
    RECONNECT_MAX_SEC,
    BookNotReady,
    MarketBookCache,
    MarketStream,
)


def _await(predicate, timeout=5.0):
    """Poll until true; offline tests have no event to wait on."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def stub_socket(monkeypatch):
    """Offline stand-in for `websocket.WebSocketApp`: records sends, no network."""
    sent = []
    apps = []

    class StubApp:
        def __init__(self, url, on_open=None, on_message=None, on_close=None,
                     on_error=None):
            assert url == ms.MARKET_WS_URL
            self.url = url
            self.on_open = on_open
            self.on_message = on_message
            self.on_close = on_close
            self.on_error = on_error
            self.closed = False
            self._park = threading.Event()
            apps.append(self)

        def run_forever(self, **kwargs):
            if self.on_open:
                self.on_open(self)
            self._park.wait(10.0)
            self._shut(1000, "socket left")

        def send(self, msg):
            if self.closed:
                raise OSError("socket is closed")
            sent.append(msg)

        def close(self):
            self._park.set()
            self._shut(1000, "closed by us")

        def _shut(self, code, reason):
            if self.closed:
                return
            self.closed = True
            if self.on_close:
                self.on_close(self, code, reason)

        def feed(self, msg):
            if self.on_message:
                self.on_message(self, msg)

    monkeypatch.setitem(sys.modules, "websocket",
                        types.SimpleNamespace(WebSocketApp=StubApp))
    return SimpleNamespace(sent=sent, apps=apps)


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


# ── T2 — socket worker, heartbeat, liveness, reconnect ─────────────────────


class _Clock:
    """A clock the test moves by hand; the loops still pace on real time."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def _frames(sent, kind="market"):
    """Parsed frames of one kind that left the socket, in order."""
    out = []
    for msg in sent:
        if not msg.startswith("{"):
            continue
        frame = json.loads(msg)
        if frame.get("type") == kind:
            out.append(frame)
    return out


def test_on_open_sends_the_initial_subscription(stub_socket):
    stream = MarketStream()
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.ensure_market("0xc2", "peru-election", "tok-x", "tok-y")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        frames = _frames(stub_socket.sent)
        assert len(frames) == 1
        assert frames[0]["assets_ids"] == ["tok-d", "tok-u", "tok-x", "tok-y"]
    finally:
        stream.stop()


def test_on_open_frame_shape_and_backoff_reset(stub_socket):
    """The locked initial frame and the reconnect ladder's reset point."""
    class _App:
        def __init__(self):
            self.sent = []

        def send(self, msg):
            self.sent.append(msg)

    stream = MarketStream()
    delays = [stream._next_backoff() for _ in range(8)]
    assert delays[:4] == [1.0, 2.0, 4.0, 8.0]
    assert delays[-1] == RECONNECT_MAX_SEC

    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    app = _App()
    stream._on_open(app)
    assert json.loads(app.sent[0]) == {"assets_ids": ["tok-d", "tok-u"],
                                      "type": "market"}
    # A successful open is the ladder's reset point.
    assert stream._next_backoff() == 1.0


def test_heartbeat_sends_ping_text_frames(stub_socket):
    stream = MarketStream(ping_sec=0.01)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.start()
    try:
        assert _await(lambda: stub_socket.sent.count(PING_TEXT) >= 2)
        last = stub_socket.sent.index(PING_TEXT)
        assert stub_socket.sent[last] == "PING"  # plain text, not a JSON frame
    finally:
        stream.stop()


def test_pong_keeps_the_connection_ready(stub_socket):
    clock = _Clock()
    # A long ping interval parks the loop; the test steps the heartbeat itself.
    stream = MarketStream(time_fn=clock, ping_sec=60.0)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        app = stub_socket.apps[0]

        clock.now += LIVE_WINDOW_SEC - 1.0
        stream._heartbeat_tick()
        assert app.closed is False
        assert stub_socket.sent[-1] == PING_TEXT

        clock.now += LIVE_WINDOW_SEC  # the liveness window is long gone
        app.feed("PONG")  # ... and one PONG reply refreshes it
        stream._heartbeat_tick()
        assert app.closed is False
        assert stub_socket.sent[-1] == PING_TEXT
    finally:
        stream.stop()


def test_a_silent_socket_is_closed_by_the_liveness_window(stub_socket):
    clock = _Clock()
    stream = MarketStream(time_fn=clock, ping_sec=0.01)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        app = stub_socket.apps[0]
        assert _await(lambda: PING_TEXT in stub_socket.sent)

        clock.now += LIVE_WINDOW_SEC + 1.0
        assert _await(lambda: app.closed)
        assert stream.connected is False
    finally:
        stream.stop()


def test_close_drops_every_book_and_schedules_a_reconnect(stub_socket, monkeypatch):
    monkeypatch.setattr(ms, "RECONNECT_MIN_SEC", 0.01)
    stream = MarketStream()
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.start()
    try:
        assert _await(lambda: len(stub_socket.apps) == 1 and stream.connected)
        stub_socket.apps[0].feed(
            json.dumps(_book_frame("tok-u", [_lvl(0.46)], [_lvl(0.50)])))
        assert stream._cache.book("tok-u")["best_bid"] == 0.46

        stub_socket.apps[0].close()  # the venue drops the socket
        # A dead feed must not keep serving prices.
        with pytest.raises(BookNotReady):
            stream._cache.book("tok-u")
        assert stream.connected is False

        assert _await(lambda: len(_frames(stub_socket.sent)) == 2)
        assert stub_socket.apps[1] is not stub_socket.apps[0]
        assert stream.connected is True
    finally:
        stream.stop()


def test_stop_joins_the_worker_and_the_heartbeat(stub_socket):
    stream = MarketStream()
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.start()
    assert _await(lambda: stream.connected)
    assert stream._thread.is_alive() and stream._heartbeat.is_alive()

    stream.stop()
    assert stream.connected is False
    assert not [t for t in threading.enumerate()
                if t.name.startswith("market-stream")]

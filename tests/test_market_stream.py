"""Live market stream engine (#648): stream-folded books, offline.

Every test is offline: frames are injected directly, the socket is stubbed,
and nothing here touches the venue or `data/orders.db`.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from core_brain import markets
from core_brain import market_stream as ms
from core_brain.market_stream import (
    LIVE_WINDOW_SEC,
    PING_TEXT,
    READ_LATENCY_BUDGET_SEC,
    RECONNECT_MAX_SEC,
    SNAPSHOT_TIMEOUT_SEC,
    BookNotReady,
    MarketBookCache,
    MarketStream,
)

FROZEN_ACTIONS = {
    "market_stream_connected", "market_stream_reconnect",
    "market_stream_subscribe", "market_stream_unsubscribe",
    "market_stream_snapshot", "market_stream_error", "market_stream_tick",
}


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


def _ops(sent, operation):
    """Parsed differential frames (`subscribe` / `unsubscribe`) in order."""
    out = []
    for msg in sent:
        if not msg.startswith("{"):
            continue
        frame = json.loads(msg)
        if frame.get("operation") == operation:
            out.append(frame)
    return out


class _Recorder:
    """Stands in for `cycle_stream.emit`: keeps every call as one dict."""

    def __init__(self):
        self.calls = []

    def __call__(self, cycle, phase, action, **kw):
        self.calls.append({"cycle": cycle, "phase": phase, "action": action, **kw})

    def of(self, action):
        return [c for c in self.calls if c["action"] == action]

    def actions(self):
        return [c["action"] for c in self.calls]


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
        for token in ("tok-u", "tok-d"):
            app.feed(json.dumps(_book_frame(token, [_lvl(0.46)], [_lvl(0.50)])))

        clock.now += LIVE_WINDOW_SEC - 1.0
        stream._heartbeat_tick()
        assert app.closed is False
        pings = stub_socket.sent.count(PING_TEXT)
        assert pings == 1

        clock.now += LIVE_WINDOW_SEC  # the liveness window is long gone
        app.feed("PONG")  # ... and one PONG reply refreshes it
        stream._heartbeat_tick()
        assert app.closed is False
        assert stub_socket.sent.count(PING_TEXT) == pings + 1
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


# ── T3 — dynamic universe + telemetry ──────────────────────────────────────


def test_ensure_market_subscribes_only_the_new_pair(stub_socket):
    rec = _Recorder()
    stream = MarketStream(emit_fn=rec)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        stream.ensure_market("0xc2", "peru-election", "tok-x", "tok-y")
        assert _await(lambda: _ops(stub_socket.sent, "subscribe"))

        assert _ops(stub_socket.sent, "subscribe") == [
            {"assets_ids": ["tok-x", "tok-y"], "operation": "subscribe"}]
        # The initial frame is sent once per connection, never re-sent.
        assert len(_frames(stub_socket.sent)) == 1

        # Registering the same market again is a no-op.
        stream.ensure_market("0xc2", "peru-election", "tok-x", "tok-y")
        assert len(_ops(stub_socket.sent, "subscribe")) == 1
        assert [c["extra"]["slug"] for c in rec.of("market_stream_subscribe")] == [
            "brazil-election", "peru-election"]
    finally:
        stream.stop()


def test_ensure_market_replays_on_the_next_open_when_offline(stub_socket):
    stream = MarketStream()
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.ensure_market("0xc2", "peru-election", "tok-x", "tok-y")
    assert stub_socket.sent == []  # no socket yet: nothing to send to

    stream.start()
    try:
        assert _await(lambda: stream.connected)
        assert _frames(stub_socket.sent)[0]["assets_ids"] == [
            "tok-d", "tok-u", "tok-x", "tok-y"]
        assert _ops(stub_socket.sent, "subscribe") == []
    finally:
        stream.stop()


def test_retain_markets_unsubscribes_and_drops_only_the_removed_books(stub_socket):
    rec = _Recorder()
    stream = MarketStream(emit_fn=rec)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.ensure_market("0xc2", "peru-election", "tok-x", "tok-y")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        app = stub_socket.apps[0]
        app.feed(json.dumps(_book_frame("tok-u", [_lvl(0.46)], [_lvl(0.50)])))
        app.feed(json.dumps(_book_frame("tok-x", [_lvl(0.40)], [_lvl(0.60)])))
        assert _await(lambda: len(stream._cache.ready_tokens()) == 2)

        stream.retain_markets(["0xc2", "0xc-new"])

        assert _ops(stub_socket.sent, "unsubscribe") == [
            {"assets_ids": ["tok-d", "tok-u"], "operation": "unsubscribe"}]
        with pytest.raises(BookNotReady):
            stream._cache.book("tok-u")
        assert stream._cache.book("tok-x")["best_bid"] == 0.40
        assert stream.connected is True
        assert len(stub_socket.apps) == 1
        assert len(rec.of("market_stream_unsubscribe")) == 1
        assert rec.of("market_stream_unsubscribe")[0]["extra"]["slug"] == "brazil-election"
    finally:
        stream.stop()


def test_a_market_swap_keeps_the_socket_and_every_other_book(stub_socket):
    """The universe rolls over on the same connection: no reconnect, no losses."""
    rec = _Recorder()
    stream = MarketStream(emit_fn=rec)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.ensure_market("0xc2", "peru-election", "tok-x", "tok-y")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        app = stub_socket.apps[0]
        app.feed(json.dumps(_book_frame("tok-u", [_lvl(0.46)], [_lvl(0.50)])))
        app.feed(json.dumps(_book_frame("tok-x", [_lvl(0.40)], [_lvl(0.60)])))

        stream.retain_markets(["0xc2"])
        stream.ensure_market("0xc3", "chile-election", "tok-p", "tok-q")
        assert _await(lambda: _ops(stub_socket.sent, "subscribe"))

        assert len(stub_socket.apps) == 1  # the socket never moved
        assert stream.connected is True
        assert _ops(stub_socket.sent, "unsubscribe")[0]["assets_ids"] == ["tok-d", "tok-u"]
        assert _ops(stub_socket.sent, "subscribe")[0]["assets_ids"] == ["tok-p", "tok-q"]
        with pytest.raises(BookNotReady):
            stream._cache.book("tok-u")
        assert stream._cache.book("tok-x")["best_bid"] == 0.40
        assert rec.actions().count("market_stream_connected") == 1
    finally:
        stream.stop()


def test_snapshot_timeout_resends_once_then_reconnects_once(stub_socket):
    clock = _Clock()
    rec = _Recorder()
    stream = MarketStream(emit_fn=rec, time_fn=clock, ping_sec=60.0)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        app = stub_socket.apps[0]

        clock.now += SNAPSHOT_TIMEOUT_SEC + 1.0
        stream._heartbeat_tick()
        assert _ops(stub_socket.sent, "subscribe") == [
            {"assets_ids": ["tok-d", "tok-u"], "operation": "subscribe"}]
        assert app.closed is False
        assert [c["reason"] for c in rec.of("market_stream_error")] == ["snapshot_timeout"]

        clock.now += SNAPSHOT_TIMEOUT_SEC + 1.0
        stream._heartbeat_tick()
        assert app.closed is True  # one controlled reconnect, not a resend storm
        assert len(_ops(stub_socket.sent, "subscribe")) == 1
        assert len(rec.of("market_stream_error")) == 2
        assert rec.of("market_stream_error")[-1]["extra"]["action"] == "reconnect"
        # The payload names the market that went quiet, never 77-digit ids.
        assert rec.of("market_stream_error")[0]["extra"]["markets"] == [
            "brazil-election"]
        assert "tok-" not in json.dumps(rec.of("market_stream_error"))

        # The token held the whole time: fail closed, never a stale quote.
        with pytest.raises(BookNotReady):
            stream._cache.book("tok-u")
    finally:
        stream.stop()


def test_snapshot_stops_the_timeout_clock(stub_socket):
    """A token that answers stops counting as missing."""
    clock = _Clock()
    rec = _Recorder()
    stream = MarketStream(emit_fn=rec, time_fn=clock, ping_sec=60.0)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        app = stub_socket.apps[0]
        for token in ("tok-u", "tok-d"):
            app.feed(json.dumps(_book_frame(token, [_lvl(0.46)], [_lvl(0.50)])))
        assert _await(lambda: len(rec.of("market_stream_snapshot")) == 1)

        clock.now += SNAPSHOT_TIMEOUT_SEC + 1.0
        stream._heartbeat_tick()
        assert _ops(stub_socket.sent, "subscribe") == []
        assert app.closed is False
        assert rec.of("market_stream_error") == []
        snap = rec.of("market_stream_snapshot")[0]
        # One line per market, carrying both books -- not one line per token.
        assert snap["extra"] == {"slug": "brazil-election", "books": 2}
        assert snap["market_slug"] == "brazil-election"
    finally:
        stream.stop()


def test_lifecycle_events_carry_the_documented_actions_and_service(stub_socket,
                                                                  monkeypatch):
    monkeypatch.setattr(ms, "RECONNECT_MIN_SEC", 0.01)
    clock = _Clock()
    rec = _Recorder()
    stream = MarketStream(emit_fn=rec, time_fn=clock, ping_sec=60.0, tick_sec=10.0)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        app = stub_socket.apps[0]
        for token in ("tok-u", "tok-d"):
            app.feed(json.dumps(_book_frame(token, [_lvl(0.46)], [_lvl(0.50)])))
        assert _await(lambda: len(rec.of("market_stream_snapshot")) == 1)
        app.feed(json.dumps(_change_frame(_change(
            "tok-u", "BUY", "0.47", "5", best_bid="0.60", best_ask="0.61"))))
        app.feed("this is not a frame at all")
        clock.now += 11.0
        stream._heartbeat_tick()
        stream.ensure_market("0xc2", "peru-election", "tok-x", "tok-y")
        stream.retain_markets(["0xc2"])

        app.close()  # the venue drops us: the reconnect event follows
        assert _await(lambda: rec.of("market_stream_reconnect"))

        for call in rec.calls:
            assert call["cycle"] == 0
            assert call["phase"] == "stream"
            assert call["service"] == "market_stream"
            assert call["action"] in FROZEN_ACTIONS
        assert set(rec.actions()) == FROZEN_ACTIONS
        assert rec.of("market_stream_reconnect")[0]["extra"]["delay_sec"] == 0.01
        assert rec.of("market_stream_reconnect")[0]["extra"]["close_code"] == 1000
        assert rec.of("market_stream_connected")[0]["extra"]["url"] == ms.MARKET_WS_URL
        assert [c["reason"] for c in rec.of("market_stream_error")] == [
            "frame_error", "level_gap"]
    finally:
        stream.stop()


def test_tick_summaries_are_rate_limited(stub_socket):
    clock = _Clock()
    rec = _Recorder()
    stream = MarketStream(emit_fn=rec, time_fn=clock, ping_sec=60.0, tick_sec=10.0)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        app = stub_socket.apps[0]
        for token in ("tok-u", "tok-d"):
            app.feed(json.dumps(_book_frame(token, [_lvl(0.46)], [_lvl(0.50)])))
        assert _await(lambda: len(stream._cache.ready_tokens()) == 2)

        clock.now += 11.0
        stream._heartbeat_tick()
        stream._heartbeat_tick()  # same instant: never a second summary
        assert len(rec.of("market_stream_tick")) == 1

        clock.now += 11.0
        stream._heartbeat_tick()
        ticks = rec.of("market_stream_tick")
        assert len(ticks) == 2
        first, last = ticks[0]["extra"], ticks[1]["extra"]
        assert first["books"] == 2
        assert first["frames"] == 2  # the two snapshots in this window
        assert first["level_gap"] == 0
        assert first["read_us_p50"] > 0
        assert last["frames"] == 0  # a window delta, not a running total
    finally:
        stream.stop()


def test_emit_failure_never_kills_the_stream_thread(stub_socket):
    class Boom:
        """An emitter that always raises, and counts the attempts."""

        def __init__(self):
            self.calls = 0

        def __call__(self, *a, **kw):
            self.calls += 1
            raise RuntimeError("the ring is gone")

    boom = Boom()
    stream = MarketStream(emit_fn=boom, ping_sec=0.01, tick_sec=0.01)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        # Telemetry raised on the worker *and* on the heartbeat, repeatedly.
        assert _await(lambda: boom.calls > 1)
        stream.ensure_market("0xc2", "peru-election", "tok-x", "tok-y")
        assert _await(lambda: _ops(stub_socket.sent, "subscribe"))
        assert stream.connected is True
        assert stream._thread.is_alive() and stream._heartbeat.is_alive()
    finally:
        stream.stop()


# ── T4 — seam adapter, hard-stop guard, operator probe ─────────────────────


def test_fetch_books_matches_the_venue_seam_signature():
    """`VenueSeam.fetch_books(clob_host, token_id)` — same two arguments."""
    import inspect

    params = list(inspect.signature(MarketStream.fetch_books).parameters)
    assert params == ["self", "clob_host", "token_id"]

    stream = MarketStream()
    stream._connected = True
    stream._alive_at = stream._time()
    stream._cache.apply_frame(_book_frame("tok-u", [_lvl(0.46)], [_lvl(0.50)]))
    # The host is ignored: the book is local, and that is the whole point.
    assert stream.fetch_books("http://anything", "tok-u")["best_bid"] == 0.46
    with pytest.raises(BookNotReady):
        stream.fetch_books("http://anything", "tok-nope")


def test_a_seam_built_on_the_stream_never_calls_full_book(monkeypatch):
    """A quoting pass over the stream reaches no HTTP book endpoint."""
    from core_brain import quotes

    stream = MarketStream()
    stream._connected = True
    stream._alive_at = stream._time()
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream._cache.apply_frame(_book_frame("tok-u", [_lvl(0.46)], [_lvl(0.50)]))
    stream._cache.apply_frame(_book_frame("tok-d", [_lvl(0.50)], [_lvl(0.54)]))

    def no_http(*a, **kw):
        raise AssertionError("a book read reached HTTP")

    monkeypatch.setattr(markets._SESSION, "get", no_http)
    market = SimpleNamespace(condition_id="0xc1", up_token="tok-u",
                             down_token="tok-d", t_remaining=lambda: 0.0)
    seen = {}

    def decide(cfg, up_book, down_book, inv, *rest, **kw):
        seen["up"], seen["down"] = up_book, down_book
        return [{"side": "UP"}], "quoted"

    def run():
        return quotes.evaluate_market_quote(
            "0xc1", None, "http://clob",
            fetch_market=lambda cid: market,
            fetch_books=stream.fetch_books,
            inventory_for=lambda m: None,
            decide=decide,
        )

    result = run()
    assert result.intents == [{"side": "UP"}]
    assert seen["up"]["token_id"] == "tok-u"
    assert seen["up"]["best_bid"] == 0.46 and seen["down"]["best_ask"] == 0.54

    # A dead feed holds the orders: the seam fails closed, never stale.
    stream._cache.clear_all()
    with pytest.raises(quotes.MarketQuoteError):
        run()


def test_single_buy_saver_never_reaches_the_stream():
    """The locked hard-stop invariant: exits keep their own fresh venue read."""
    root = Path(__file__).resolve().parent.parent / "core_brain"
    for name in ("single_buy_saver.py", "shadow_run.py", "shadow_exec.py"):
        assert "market_stream" not in (root / name).read_text(encoding="utf-8")


def test_one_snapshot_line_per_market(stub_socket):
    """Two books, one line. And a re-land after a swap still reports once."""
    rec = _Recorder()
    stream = MarketStream(emit_fn=rec, ping_sec=60.0)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.ensure_market("manual:tok-solo", "manual", "tok-solo", "tok-solo")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        app = stub_socket.apps[0]
        for token in ("tok-u", "tok-d", "tok-solo"):
            app.feed(json.dumps(_book_frame(token, [_lvl(0.46)], [_lvl(0.50)])))
        assert _await(lambda: len(rec.of("market_stream_snapshot")) == 2)

        pairs = {c["extra"]["slug"]: c["extra"]["books"]
                 for c in rec.of("market_stream_snapshot")}
        assert pairs == {"brazil-election": 2, "manual": 1}

        # More deltas must not produce more lines.
        app.feed(json.dumps(_change_frame(_change("tok-u", "BUY", "0.47", "5"))))
        assert len(rec.of("market_stream_snapshot")) == 2

        # Rolled off and back on: one more line, not zero and not two.
        stream.retain_markets(["manual:tok-solo"])
        stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
        for token in ("tok-u", "tok-d"):
            app.feed(json.dumps(_book_frame(token, [_lvl(0.48)], [_lvl(0.50)])))
        assert _await(lambda: len(rec.of("market_stream_snapshot")) == 3)
    finally:
        stream.stop()


def test_stream_lines_follow_the_tag_convention():
    """`[TAG] subject · facts`, shared with the dashboard card."""
    line = ms._stream_line
    ev = lambda action, extra=None, **kw: (  # noqa: E731
        line(action, {"extra": extra or {}, **kw}))

    assert ev("market_stream_connected", {"tokens": ["t1", "t2", "t3", "t4"]}) \
        == "[OPEN] 4 tokens"
    assert ev("market_stream_connected", {"tokens": ["t1"]}) == "[OPEN] 1 token"
    assert ev("market_stream_reconnect", {"delay_sec": 2.0, "close_code": 1006},
              reason="socket left") == "[WAIT] 2s · socket left"
    assert ev("market_stream_reconnect", {"delay_sec": 30}) == "[WAIT] 30s"
    assert ev("market_stream_subscribe",
              {"slug": "brazil-election", "tokens": ["t1", "t2"]}) \
        == "[+]    brazil-election · +2 tokens"
    assert ev("market_stream_unsubscribe",
              {"slug": "brazil-election", "tokens": ["t1"]}) \
        == "[-]    brazil-election · -1 token"
    assert ev("market_stream_snapshot",
              {"slug": "brazil-election", "books": 2}) \
        == "[BOOK] brazil-election · +2 books"
    assert ev("market_stream_error", {"markets": ["brazil-election"]},
              reason="snapshot_timeout") \
        == "[WARN] snapshot_timeout · brazil-election"
    assert ev("market_stream_error", {"detail": "not json"}, reason="frame_error") \
        == "[WARN] frame_error · not json"
    assert ev("market_stream_error", {"count": 3}, reason="level_gap") \
        == "[WARN] level_gap · 3 gaps"
    assert ev("market_stream_tick",
              {"books": 6, "frames": 53, "level_gap": 0, "read_us_p50": 4.7}) \
        == "[TICK] 6 books · 53 frames · 4.7us read"
    assert ev("market_stream_tick",
              {"books": 1, "frames": 1, "level_gap": 2, "read_us_p50": 41.0}) \
        == "[TICK] 1 book · 1 frame · 41us read · 2 gaps"
    assert ev("market_stream_something_new") == "[?]    something_new"

    # The convention, not just the content: every tag pads to one column, and
    # no line ever leaks an action name, a JSON blob or a token id.
    for action in ms.LOG_TAGS:
        out = ev(action, {"tokens": ["t"], "slug": "s", "books": 1,
                          "delay_sec": 1, "detail": "d", "count": 1,
                          "markets": ["s"], "frames": 1, "read_us_p50": 1.0})
        assert re.match(r"^\[[^\]]{1,4}\]\s+\S", out), out
        assert out[6] == " " and out[7] != " ", out  # facts in one column
        assert "market_stream" not in out
        assert "{" not in out and "}" not in out
    assert ms._skip_line({"slug": "ufc-eri3-jas3-2026-10-17"}) == (
        "[SKIP] ufc-eri3-jas3-2026-10-17 · no venue market")
    assert ms._tagged("RUN", "wss://x · 180s") == "[RUN]  wss://x · 180s"
    assert ms._tagged("STOP", "interrupted") == "[STOP] interrupted"

    # The dashboard formats the same seven tags.
    app_js = (Path(__file__).resolve().parent.parent / "dashboard" / "static"
              / "app.js").read_text(encoding="utf-8")
    for tag in ms.LOG_TAGS.values():
        assert f"'{tag}'" in app_js or f'"{tag}"' in app_js, tag


def test_probe_parses_the_markets_file_and_bounds_its_run(stub_socket, tmp_path):
    rows = [{"cid": "0xc1", "slug": "brazil-election"},
            {"cid": "0xc2", "slug": "peru-election"},
            {"slug": "a-row-with-no-condition-id"},
            {"cid": "0xc3", "slug": "chile-election"}]
    path = tmp_path / "markets.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert [r["cid"] for r in ms._probe_rows(str(path), 2)] == ["0xc1", "0xc2"]
    assert [r["cid"] for r in ms._probe_rows(str(path), 0)] == [
        "0xc1", "0xc2", "0xc3"]

    bad = tmp_path / "bad.json"
    bad.write_text('{"markets": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        ms._probe_rows(str(bad), 2)

    rec = _Recorder()
    assert ms.main(["--tokens", "tok-u,tok-d"], emit_fn=rec) == 2  # --listen is required

    started = time.monotonic()
    assert ms.main(["--listen", "--tokens", "tok-u,tok-d", "--minutes", "0.01",
                    "--refresh-sec", "0.05"], emit_fn=rec) == 0
    assert time.monotonic() - started < 5.0
    assert _frames(stub_socket.sent)[0]["assets_ids"] == ["tok-d", "tok-u"]
    assert len(stub_socket.apps) == 1
    assert not [t for t in threading.enumerate()
                if t.name.startswith("market-stream")]

    # The probe's default emitter is the real cycle-stream feed.
    src = (Path(__file__).resolve().parent.parent / "core_brain"
           / "market_stream.py").read_text(encoding="utf-8")
    assert "import emit as emit_cycle_event" in src


def test_fetch_books_fails_closed_when_disconnected_or_stale():
    clock = _Clock()
    stream = MarketStream(time_fn=clock)
    # 1. When disconnected, fetch_books raises BookNotReady immediately.
    assert stream.connected is False
    with pytest.raises(BookNotReady, match="market stream is not connected"):
        stream.fetch_books("http://clob", "tok-u")

    # 2. When connected, book is ready.
    stream._connected = True
    stream._alive_at = clock.now
    stream._cache.apply_frame(_book_frame("tok-u", [_lvl(0.46)], [_lvl(0.50)]))
    assert stream.fetch_books("http://clob", "tok-u")["best_bid"] == 0.46

    # 3. When time advances past LIVE_WINDOW_SEC without any frame or heartbeat:
    clock.now += ms.LIVE_WINDOW_SEC + 1.0
    with pytest.raises(BookNotReady, match="market stream went silent"):
        stream.fetch_books("http://clob", "tok-u")


def test_ensure_market_drops_stale_cache_and_on_message_drops_stray_tokens(stub_socket):
    rec = _Recorder()
    stream = MarketStream(emit_fn=rec)
    # Simulate a leftover book from an earlier session sitting in cache.
    stream._cache.apply_frame(_book_frame("tok-u", [_lvl(0.46)], [_lvl(0.50)]))
    assert stream._cache.book("tok-u")["best_bid"] == 0.46

    # Subscribing freshly drops the old book so it cannot be read prematurely.
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    with pytest.raises(BookNotReady):
        stream._cache.book("tok-u")

    # Start socket to simulate receiving a stray frame for an unsubscribed token.
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        app = stub_socket.apps[0]
        # Feed frame for stray token "tok-stray" not in _want.
        app.feed(json.dumps(_book_frame("tok-stray", [_lvl(0.10)], [_lvl(0.20)])))
        with pytest.raises(BookNotReady):
            stream._cache.book("tok-stray")
    finally:
        stream.stop()


def test_snapshot_timeout_abandons_token_after_one_reconnect(stub_socket):
    """A permanently missing snapshot reconnects once, then abandons without loop."""
    clock = _Clock()
    rec = _Recorder()
    stream = MarketStream(emit_fn=rec, time_fn=clock, ping_sec=60.0)
    stream.ensure_market("0xc1", "brazil-election", "tok-u", "tok-d")
    stream.start()
    try:
        assert _await(lambda: stream.connected)
        app1 = stub_socket.apps[0]

        # First timeout: resend subscribe.
        clock.now += SNAPSHOT_TIMEOUT_SEC + 1.0
        stream._heartbeat_tick()
        assert app1.closed is False
        assert [c["reason"] for c in rec.of("market_stream_error")] == ["snapshot_timeout"]

        # Second timeout: controlled reconnect.
        clock.now += SNAPSHOT_TIMEOUT_SEC + 1.0
        stream._heartbeat_tick()
        assert app1.closed is True
        assert rec.of("market_stream_error")[-1]["extra"]["action"] == "reconnect"

        # Now simulate reconnect with app2.
        assert _await(lambda: len(stub_socket.apps) >= 2)
        app2 = stub_socket.apps[1]
        assert stream.connected is True

        # Healthy token tok-healthy is also added and receives its snapshot on app2.
        stream.ensure_market("0xc2", "healthy-market", "tok-h-u", "tok-h-d")
        app2.feed(json.dumps(_book_frame("tok-h-u", [_lvl(0.40)], [_lvl(0.60)])))
        app2.feed(json.dumps(_book_frame("tok-h-d", [_lvl(0.40)], [_lvl(0.60)])))
        assert _await(lambda: stream._cache.book("tok-h-u")["best_bid"] == 0.40)

        # On app2, the bad tokens tok-u, tok-d timeout again (resend 1).
        clock.now += SNAPSHOT_TIMEOUT_SEC + 1.0
        stream._heartbeat_tick()
        assert app2.closed is False

        # Next timeout on app2: since tok-u and tok-d already had their reconnect,
        # they are abandoned. The socket app2 is NOT closed, preserving tok-h-u!
        clock.now += SNAPSHOT_TIMEOUT_SEC + 1.0
        stream._heartbeat_tick()
        assert app2.closed is False
        assert rec.of("market_stream_error")[-1]["extra"]["action"] == "abandoned"

        # Healthy market is still alive in cache on the open connection.
        assert stream._cache.book("tok-h-u")["best_bid"] == 0.40
        # Bad market is still not ready.
        with pytest.raises(BookNotReady):
            stream._cache.book("tok-u")
    finally:
        stream.stop()


def test_probe_refresh_ignores_oserror_or_malformed_file(monkeypatch, capsys):
    """A half-written file during probe refresh does not crash the probe."""
    rec = _Recorder()
    calls = 0

    def mock_probe_rows(path, limit):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [{"cid": "0xc1", "slug": "brazil-election"}]
        raise ValueError("corrupt json during save")

    monkeypatch.setattr(ms, "_probe_rows", mock_probe_rows)
    monkeypatch.setattr(markets, "fetch_pinned_market",
                        lambda cid: SimpleNamespace(up_token="tok-u", down_token="tok-d"))

    assert ms.main(["--listen", "--markets", "runtime/markets.json",
                    "--minutes", "0.05", "--refresh-sec", "0.01"], emit_fn=rec) == 0
    captured = capsys.readouterr()
    assert "[refresh skipped]" in captured.out
    assert "corrupt json during save" in captured.out

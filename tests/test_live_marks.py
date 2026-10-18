"""Live mark cache (#427): shared, read-only venue mids for held tokens.

All offline: recorded venue messages are injected directly, no socket.
"""
from __future__ import annotations

import pytest

from core_brain.live_marks import (
    LiveMarkCache,
    live_marks_source,
    update_wanted_from_kpi,
)


def _book(token, bids, asks):
    return {"asset_id": token, "bids": bids, "asks": asks}


def _lvl(price, size=10.0):
    return {"price": price, "size": size}


def test_snapshot_produces_mid():
    cache = LiveMarkCache()
    cache.update_wanted({"tok-u"})
    cache.apply_message(_book("tok-u", [_lvl(0.47)], [_lvl(0.50)]))
    seq, marks = cache.snapshot()
    assert seq == 1
    assert len(marks) == 1
    m = marks[0]
    assert (m["token_id"], m["bid"], m["ask"], m["mid"]) == ("tok-u", 0.47, 0.50, 0.485)


def test_list_form_message_is_accepted():
    cache = LiveMarkCache()
    cache.update_wanted({"tok-u"})
    cache.apply_message([_book("tok-u", [_lvl(0.47)], [_lvl(0.50)])])
    _, marks = cache.snapshot()
    assert [m["mid"] for m in marks] == [0.485]


def test_level_change_moves_best_and_zero_size_removes():
    cache = LiveMarkCache()
    cache.update_wanted({"tok-u"})
    cache.apply_message(_book("tok-u", [_lvl(0.47), _lvl(0.46)], [_lvl(0.50)]))
    assert cache.snapshot()[1][0]["bid"] == 0.47
    cache.apply_message({"asset_id": "tok-u", "bids": [_lvl(0.47, 0.0)]})
    assert cache.snapshot()[1][0]["bid"] == 0.46


def test_crossed_equal_and_out_of_range_remove_the_entry():
    cache = LiveMarkCache()
    cache.update_wanted({"tok-u"})
    cache.apply_message(_book("tok-u", [_lvl(0.47)], [_lvl(0.50)]))
    assert len(cache.snapshot()[1]) == 1
    for bids, asks in [
        ([_lvl(0.50)], [_lvl(0.47)]),   # crossed
        ([_lvl(0.48)], [_lvl(0.48)]),   # equal
        ([_lvl(-0.1)], [_lvl(0.50)]),   # out of range
        ([_lvl(0.47)], [_lvl(1.50)]),   # out of range
    ]:
        cache.apply_message(_book("tok-u", bids, asks))
        assert cache.snapshot()[1] == [], f"{bids}/{asks} must drop the entry"


def test_non_finite_prices_drop_the_entry():
    cache = LiveMarkCache()
    cache.update_wanted({"tok-u"})
    cache.apply_message(_book("tok-u", [_lvl(0.47)], [_lvl(0.50)]))
    cache.apply_message(_book("tok-u", [_lvl(float("nan"))], [_lvl(0.50)]))
    assert cache.snapshot()[1] == []


def test_unknown_tokens_are_ignored():
    cache = LiveMarkCache()
    cache.update_wanted({"tok-u"})
    cache.apply_message(_book("tok-other", [_lvl(0.47)], [_lvl(0.50)]))
    assert cache.snapshot() == (0, [])


def test_disconnect_clears_and_raises_reset_with_rising_seq():
    cache = LiveMarkCache()
    cache.update_wanted({"tok-u"})
    cache.apply_message(_book("tok-u", [_lvl(0.47)], [_lvl(0.50)]))
    seq_before, _ = cache.snapshot()
    gen_before = cache.reset_generation
    cache.disconnect()
    seq_after, marks = cache.snapshot()
    assert marks == []
    assert seq_after > seq_before
    assert cache.reset_generation == gen_before + 1


def test_wanted_set_comes_from_held_unfinished_markets_only():
    payload = {"by_market": {
        "held": {"resolved": False, "up_sh": 5, "dn_sh": 0,
                 "quotes": [{"token_id": "tok-u", "price": 0.47}],
                 "fills": [{"token_id": "tok-u"}]},
        "empty": {"resolved": False, "up_sh": 0, "dn_sh": 0,
                  "quotes": [{"token_id": "tok-e", "price": 0.5}], "fills": []},
        "done": {"resolved": True, "up_sh": 5, "dn_sh": 5,
                 "quotes": [{"token_id": "tok-d", "price": 0.5}], "fills": []},
    }}
    cache = LiveMarkCache()
    changed = update_wanted_from_kpi(cache, payload, now=1000.0)
    assert changed is True
    assert cache.wanted() == {"tok-u"}
    # Same payload again: no change, no expiry yet.
    assert update_wanted_from_kpi(cache, payload, now=1010.0) is False
    # Unmentioned for past the TTL: expired.
    assert update_wanted_from_kpi(cache, {"by_market": {}}, now=2000.0) is True
    assert cache.wanted() == set()


def test_source_switch_parses_venue_off_sim(monkeypatch):
    monkeypatch.delenv("HUNTER_MARKS_SOURCE", raising=False)
    assert live_marks_source() == "venue"
    monkeypatch.setenv("HUNTER_MARKS_SOURCE", "sim")
    assert live_marks_source() == "sim"
    monkeypatch.setenv("HUNTER_MARKS_SOURCE", "off")
    assert live_marks_source() == "off"
    monkeypatch.setenv("HUNTER_MARKS_SOURCE", "nope")
    with pytest.raises(ValueError, match="HUNTER_MARKS_SOURCE"):
        live_marks_source()


def test_venue_subscribes_on_open_and_sends_differential_frames(monkeypatch):
    """The socket subscribes on open and sends differential frames on drift.

    Offline: a stub WebSocketApp records what leaves the socket.
    1. Initial connect: full {"assets_ids": [...], "type": "market"} frame.
    2. Unchanged set: sync_subscription() is False, 0 frames sent, no socket close.
    3. Add token: sync_subscription() is True, sends differential subscribe frame
       {"assets_ids": ["tok-b"], "operation": "subscribe"}, socket not closed,
       _sent_ids updated to {"tok-a", "tok-b"}, single app instance.
    4. Kept tokens: cache marks and generation remain intact across sync.
    5. Mixed add and remove: subscribe sent before unsubscribe, both with sorted ids.
    6. Send failure: sync_subscription() returns False, socket is closed,
       _sent_ids not corrupted, reconnect recovers with full market frame.
    """
    import json
    import time

    import core_brain.live_marks as lm

    sent = []
    apps = []

    class FakeApp:
        def __init__(self, url, on_open=None, on_message=None, on_close=None):
            assert url == lm.MARK_WS_URL
            self.on_open = on_open
            self._closed = False
            self.fail_send = False
            apps.append(self)

        def run_forever(self, **kwargs):
            self.on_open(self)
            while not self._closed and not worker._stop.is_set():
                time.sleep(0.01)

        def send(self, msg):
            if self.fail_send:
                raise OSError("socket send failure")
            sent.append(json.loads(msg))

        def close(self):
            self._closed = True

    class FakeWebSocketException(Exception):
        pass

    monkeypatch.setitem(__import__("sys").modules, "websocket",
                        type("WS", (), {"WebSocketApp": FakeApp, "WebSocketException": FakeWebSocketException}))

    cache = lm.LiveMarkCache()
    cache.update_wanted({"tok-a"})
    worker = lm.LiveMarkWorker(cache, source=lm.SOURCE_VENUE)
    worker.start()
    try:
        deadline = time.time() + 5.0
        while not sent and time.time() < deadline:
            time.sleep(0.01)
        assert len(apps) == 1
        assert apps[0]._closed is False
        assert len(sent) == 1
        assert sent[0] == {"assets_ids": ["tok-a"], "type": "market"}

        # 1. Unchanged set: no work, no frames, no close.
        assert worker.sync_subscription() is False
        assert len(sent) == 1
        assert apps[0]._closed is False

        # Set a mark on tok-a to verify it survives sync without disconnect/reset
        cache.apply_message(_book("tok-a", [_lvl(0.48)], [_lvl(0.52)]))
        seq_before, marks_before = cache.snapshot()
        gen_before = cache._reset_generation
        assert len(marks_before) == 1

        # 2. Add token: sends differential subscribe frame on existing socket
        cache.update_wanted({"tok-a", "tok-b"})
        assert worker.sync_subscription() is True
        assert len(sent) == 2
        assert sent[1] == {"assets_ids": ["tok-b"], "operation": "subscribe"}
        assert len(apps) == 1
        assert apps[0]._closed is False
        assert worker._sent_ids == frozenset({"tok-a", "tok-b"})

        # Kept token marks and reset generation are untouched
        _seq_after, marks_after = cache.snapshot()
        assert cache._reset_generation == gen_before
        assert any(m["token_id"] == "tok-a" and m["mid"] == 0.50 for m in marks_after)

        # 3. Multi-ID addition sends sorted assets_ids in subscribe frame
        cache.update_wanted({"tok-a", "tok-b", "tok-z", "tok-m"})
        assert worker.sync_subscription() is True
        assert len(sent) == 3
        assert sent[2] == {"assets_ids": ["tok-m", "tok-z"], "operation": "subscribe"}
        assert worker._sent_ids == frozenset({"tok-a", "tok-b", "tok-m", "tok-z"})

        # 4. Mixed add and remove: tok-b expires via TTL clock, tok-c added.
        # Subscribe for tok-c must precede unsubscribe for tok-b, tok-m, tok-z.
        cache.update_wanted({"tok-b", "tok-m", "tok-z"}, now=1000.0)
        cache.update_wanted({"tok-a"}, now=1100.0)
        assert cache.expire_wanted(now=1220.0) is True
        cache.update_wanted({"tok-a", "tok-c"})
        assert worker.sync_subscription() is True
        assert len(sent) == 5
        assert sent[3] == {"assets_ids": ["tok-c"], "operation": "subscribe"}
        assert sent[4] == {"assets_ids": ["tok-b", "tok-m", "tok-z"], "operation": "unsubscribe"}
        assert len(apps) == 1
        assert apps[0]._closed is False
        assert worker._sent_ids == frozenset({"tok-a", "tok-c"})

        # 5. Send failure: socket closed, returns False, reconnect recovers
        apps[0].fail_send = True
        cache.update_wanted({"tok-a", "tok-c", "tok-d"})
        assert worker.sync_subscription() is False
        assert apps[0]._closed is True
        assert worker._sent_ids == frozenset({"tok-a", "tok-c"})

        # Wait for reconnect loop to open second app and resubscribe full set
        deadline = time.time() + 8.0
        while len(apps) < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert len(apps) == 2
        # After reconnect, second app sends full type:market frame
        while len(sent) < 6 and time.time() < deadline:
            time.sleep(0.01)
        assert sent[-1] == {"assets_ids": ["tok-a", "tok-c", "tok-d"], "type": "market"}
    finally:
        worker.stop()
    assert not worker.running


def test_marks_since_returns_only_moved_marks():
    cache = LiveMarkCache()
    cache.update_wanted({"tok-u", "tok-v"})
    cache.apply_message(_book("tok-u", [_lvl(0.47)], [_lvl(0.50)]))
    cache.apply_message(_book("tok-v", [_lvl(0.40)], [_lvl(0.45)]))
    seq, _ = cache.snapshot()
    assert cache.marks_since(seq) == []
    assert [m["token_id"] for m in cache.marks_since(seq - 1)] == ["tok-v"]
    assert len(cache.marks_since("bogus")) == 2


def test_missing_websocket_package_fails_loud_not_silent(monkeypatch, caplog):
    """A dashboard without websocket-client must say so, not freeze marks.

    The venue import sat outside the worker's try block, so on an interpreter
    without the declared dependency the feed thread died on startup with no
    log line at all -- and every positions cell silently froze on its last
    quote mid. The worker must return cleanly and name the missing package.
    """
    import logging
    import sys

    from core_brain.live_marks import LiveMarkWorker

    monkeypatch.setitem(sys.modules, "websocket", None)
    worker = LiveMarkWorker(LiveMarkCache(), source="venue")
    with caplog.at_level(logging.ERROR, logger="core_brain.live_marks"):
        worker._run_venue()  # must not raise
    assert "websocket-client" in caplog.text

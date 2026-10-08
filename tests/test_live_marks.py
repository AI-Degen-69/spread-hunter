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
    monkeypatch.delenv("HUNTER_LIVE_MARKS_SOURCE", raising=False)
    assert live_marks_source() == "venue"
    monkeypatch.setenv("HUNTER_LIVE_MARKS_SOURCE", "sim")
    assert live_marks_source() == "sim"
    monkeypatch.setenv("HUNTER_LIVE_MARKS_SOURCE", "off")
    assert live_marks_source() == "off"
    monkeypatch.setenv("HUNTER_LIVE_MARKS_SOURCE", "nope")
    with pytest.raises(ValueError, match="HUNTER_LIVE_MARKS_SOURCE"):
        live_marks_source()


def test_venue_subscribes_on_open_and_resyncs_on_drift(monkeypatch):
    """The socket subscribes on every (re)connect and follows the wanted set.

    Offline: a stub WebSocketApp records what would leave the socket.
    Without the on-open subscribe the venue path connects but never hears
    a frame; without the sync a newly opened leg waits for the next blip.
    """
    import json
    import time

    import core_brain.live_marks as lm

    sent = []
    holder = {}

    class FakeApp:
        def __init__(self, url, on_open=None, on_message=None, on_close=None):
            assert url == lm.MARK_WS_URL
            self.on_open = on_open
            holder["app"] = self

        def run_forever(self, **kwargs):
            self.on_open(self)
            while not worker._stop.is_set():
                time.sleep(0.01)

        def send(self, msg):
            sent.append(json.loads(msg))

    monkeypatch.setitem(__import__("sys").modules, "websocket",
                        type("WS", (), {"WebSocketApp": FakeApp}))

    cache = lm.LiveMarkCache()
    cache.update_wanted({"tok-a"})
    worker = lm.LiveMarkWorker(cache, source=lm.SOURCE_VENUE)
    worker.start()
    try:
        deadline = time.time() + 5.0
        while not sent and time.time() < deadline:
            time.sleep(0.01)
        assert [m["assets_ids"] for m in sent] == [["tok-a"]]
        assert sent[0]["type"] == "market"
        # Same set again: no duplicate subscribe.
        assert worker.sync_subscription() is False
        assert len(sent) == 1
        # Holdings drift: the open socket gets the new set, no reconnect.
        cache.update_wanted({"tok-a", "tok-b"})
        assert worker.sync_subscription() is True
        assert sent[-1]["assets_ids"] == ["tok-a", "tok-b"]
    finally:
        worker.stop()
    assert not worker.running

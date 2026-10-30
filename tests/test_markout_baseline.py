"""Markout measured against a window and a peer baseline (#124).

Two problems with sampling one mid at the horizon. The bid-ask bounce alone
makes every sell look favourable and every buy look poor, and a market that
drifted for reasons that have nothing to do with this bot reads as our own
adverse selection — which, for a book we sit on passively, is the common case
rather than the corner.

So: a size-weighted VWAP over a window centred on the horizon, and a baseline
built from what everyone else who printed in the fill's window got over the same
span. The raw mid-based figure is still stored; the corrected one sits beside it.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core_brain.markout import (
    REFERENCE_WINDOW_SEC,
    excess_markout,
    peer_markout,
    sample_pending_markouts,
    vwap,
    windowed_reference,
)
from core_brain.order_registry import SCHEMA, OrderRegistry

T0 = 1_788_000_000.0


def _print(ts: float, price: float, size: float) -> dict:
    return {"timestamp": ts, "price": price, "size": size}


def test_vwap_weights_by_size_not_by_row():
    # Arrange — one big print at 0.60 and one small at 0.40.
    trades = [_print(T0, 0.60, 900.0), _print(T0 + 1, 0.40, 100.0)]

    # Act
    reference = vwap(trades, T0 - 10, T0 + 10)

    # Assert
    assert reference == pytest.approx(0.58)


def test_prints_outside_the_window_are_not_counted():
    # Arrange
    trades = [_print(T0, 0.50, 100.0), _print(T0 + 500, 0.90, 100.0)]

    # Act
    reference = windowed_reference(trades, T0, window_sec=60.0)

    # Assert
    assert reference == pytest.approx(0.50)


def test_a_window_with_no_prints_has_no_reference():
    # Arrange — substituting a mid here would mix two measurements in one
    # column without saying so.
    trades = [_print(T0 + 5000, 0.50, 100.0)]

    # Act / Assert
    assert windowed_reference(trades, T0) is None
    assert vwap([], T0 - 1, T0 + 1) is None


def test_bid_ask_bounce_averages_out_of_the_reference():
    # Arrange — alternating prints on both sides of a 0.50 mid.
    trades = [_print(T0 + i, 0.49 if i % 2 else 0.51, 100.0) for i in range(10)]

    # Act
    reference = windowed_reference(trades, T0 + 5, window_sec=REFERENCE_WINDOW_SEC)

    # Assert
    assert reference == pytest.approx(0.50)


def test_the_peer_baseline_is_what_everyone_else_got():
    # Arrange — three other buyers filled at 0.50 in our window, and the
    # reference later sits at 0.55, so the market handed them +0.05.
    trades = [_print(T0, 0.50, 100.0), _print(T0 + 1, 0.50, 100.0),
              _print(T0 + 2, 0.50, 100.0)]

    # Act
    peer = peer_markout(trades, T0 + 1, reference=0.55, direction=1.0)

    # Assert
    assert peer == pytest.approx(0.05)


def test_our_own_print_is_excluded_from_the_baseline():
    # Arrange — our fill (200 shares at 0.40) sits in the same public tape as
    # everyone else's. Counting it would let our own outcome pad the baseline
    # it is being measured against.
    trades = [_print(T0, 0.40, 200.0), _print(T0, 0.50, 100.0)]

    # Act
    peer = peer_markout(trades, T0, reference=0.55, direction=1.0,
                        fill_price=0.40, fill_size=200.0)

    # Assert — only the stranger's print, so +0.05, not the blended +0.10.
    assert peer == pytest.approx(0.05)


def test_a_window_with_nobody_but_us_has_no_baseline():
    # Arrange — with no peers there is no baseline, and a zero would claim the
    # market stood still.
    trades = [_print(T0, 0.40, 200.0)]

    # Act
    peer = peer_markout(trades, T0, reference=0.55, direction=1.0,
                        fill_price=0.40, fill_size=200.0)

    # Assert
    assert peer is None


def test_a_market_wide_drift_leaves_no_excess():
    # Arrange — the whole market moved 5c and we moved with it.
    raw = 0.05
    peer = 0.05

    # Act / Assert — the drift is the market's, not ours.
    assert excess_markout(raw, peer) == pytest.approx(0.0)


def test_beating_the_market_shows_up_as_positive_excess():
    # Arrange / Act / Assert
    assert excess_markout(0.08, 0.05) == pytest.approx(0.03)
    assert excess_markout(0.02, 0.05) == pytest.approx(-0.03)


def test_an_absent_baseline_yields_no_excess():
    # Arrange — an excess computed against a missing baseline is just the raw
    # number wearing a different name.
    assert excess_markout(0.05, None) is None
    assert excess_markout(None, 0.05) is None


# --- the sampler ------------------------------------------------------------

@pytest.fixture
def registry(tmp_path) -> OrderRegistry:
    db = tmp_path / "live.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return OrderRegistry(db)


def _seed_markout(registry: OrderRegistry, ts: float) -> int:
    con = sqlite3.connect(str(registry.db_path))
    con.execute(
        "INSERT INTO markouts (ts, condition_id, side, token_id, fill_price,"
        " size, done, run_id) VALUES (?,?,?,?,?,?,0,?)",
        (ts, "0xmarket", "BUY", "tok-up", 0.40, 200.0, "run-1"),
    )
    con.commit()
    markout_id = con.execute("SELECT MAX(id) FROM markouts").fetchone()[0]
    con.close()
    return markout_id


def _refs(registry: OrderRegistry, markout_id: int) -> dict:
    con = sqlite3.connect(str(registry.db_path))
    row = con.execute("SELECT refs_json FROM markouts WHERE id = ?",
                      (markout_id,)).fetchone()
    con.close()
    return json.loads(row[0]) if row and row[0] else {}


def test_the_reference_and_baseline_are_stored_beside_the_raw_mid(registry):
    # Arrange
    markout_id = _seed_markout(registry, T0)

    # Act
    registry.update_markout_reference(markout_id, 0, 0.55, 0.05)
    registry.update_markout_horizon(markout_id, 0, 0.56)

    # Assert — both survive; neither overwrites the other.
    stored = _refs(registry, markout_id)
    assert stored["h0"] == {"ref": 0.55, "peer": 0.05}
    con = sqlite3.connect(str(registry.db_path))
    mid = con.execute("SELECT mid_h0 FROM markouts WHERE id = ?",
                      (markout_id,)).fetchone()[0]
    con.close()
    assert mid == pytest.approx(0.56)


def test_a_second_horizon_does_not_erase_the_first(registry):
    # Arrange
    markout_id = _seed_markout(registry, T0)
    registry.update_markout_reference(markout_id, 0, 0.55, 0.05)

    # Act
    registry.update_markout_reference(markout_id, 1, 0.60, 0.10)

    # Assert
    stored = _refs(registry, markout_id)
    assert set(stored) == {"h0", "h1"}
    assert stored["h0"]["ref"] == 0.55


def test_an_unmeasurable_window_is_stored_as_null(registry):
    # Arrange — the horizon window held no prints.
    markout_id = _seed_markout(registry, T0)

    # Act
    registry.update_markout_reference(markout_id, 0, None, None)

    # Assert — recorded as unmeasured, not as zero.
    assert _refs(registry, markout_id)["h0"] == {"ref": None, "peer": None}


def test_a_tape_that_will_not_answer_leaves_the_raw_markout_alone(registry, monkeypatch):
    # Arrange — the sampler's contract is that a failed read leaves NULLs and
    # never blocks. A tape failure must not cost us the mid-based figure.
    markout_id = _seed_markout(registry, T0 - 4000)

    class _Market:
        up_token = "tok-up"
        down_token = "tok-down"

    monkeypatch.setattr("core_brain.markets.fetch_pinned_market",
                        lambda *a, **k: _Market())
    monkeypatch.setattr("core_brain.markets.full_book",
                        lambda host, token: {"best_bid": 0.55, "best_ask": 0.57})

    def _boom(token_id, condition_id=None):
        raise OSError("tape unreachable")

    # Act
    updated = sample_pending_markouts(registry, now_sec=T0, trades_fn=_boom)

    # Assert — the mid was still recorded; the reference simply is not there.
    assert updated >= 1
    assert _refs(registry, markout_id) == {}
    con = sqlite3.connect(str(registry.db_path))
    mid = con.execute("SELECT mid_h0 FROM markouts WHERE id = ?",
                      (markout_id,)).fetchone()[0]
    con.close()
    assert mid == pytest.approx(0.56)


def test_the_sampler_records_the_reference_when_the_tape_answers(registry, monkeypatch):
    # Arrange — our fill at 0.40, a peer at 0.50 in the same window, and the
    # tape sitting at 0.55 around the 300s horizon.
    fill_ts = T0 - 4000
    markout_id = _seed_markout(registry, fill_ts)

    class _Market:
        up_token = "tok-up"
        down_token = "tok-down"

    monkeypatch.setattr("core_brain.markets.fetch_pinned_market",
                        lambda *a, **k: _Market())
    monkeypatch.setattr("core_brain.markets.full_book",
                        lambda host, token: {"best_bid": 0.55, "best_ask": 0.57})

    trades = [
        _print(fill_ts, 0.40, 200.0),        # ours
        _print(fill_ts, 0.50, 100.0),        # a peer
        _print(fill_ts + 300, 0.55, 100.0),  # around the first horizon
    ]

    # Act
    sample_pending_markouts(registry, now_sec=T0, trades_fn=lambda token, cid=None: trades)

    # Assert
    stored = _refs(registry, markout_id)["h0"]
    assert stored["ref"] == pytest.approx(0.55)
    assert stored["peer"] == pytest.approx(0.05)


# --- the aggregate the dashboard can read ----------------------------------

def _seed_fill(registry: OrderRegistry, price: float = 0.40,
               size: float = 200.0) -> None:
    """A filled order and its fill, so `filled_sh` is not zero.

    The raw adverse-selection figure divides by filled shares, so without this
    the raw number is None and only half the comparison is visible.
    """
    con = sqlite3.connect(str(registry.db_path))
    con.execute(
        "INSERT INTO orders (id, order_id, condition_id, token_id, side, price,"
        " original_size, status, posted_ts, last_polled_ts, run_id)"
        " VALUES ('o1','o1','0xmarket','tok-up','BUY',?,?,'filled',?,?,'run-1')",
        (price, size, int(T0 * 1000), int(T0 * 1000)),
    )
    con.execute(
        "INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts, run_id)"
        " VALUES ('t1','o1',?,?,?,'run-1')",
        (size, price, int(T0 * 1000)),
    )
    con.commit()
    con.close()


def test_kpi_reports_the_excess_beside_the_raw_drift(registry, tmp_path, monkeypatch):
    # Arrange — one fill at 0.40, reference 0.55, and the market handed peers
    # +0.05 over the same span. Raw drift is +0.15; excess is +0.10.
    from core_brain import kpi as kpi_mod

    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    _seed_fill(registry)
    markout_id = _seed_markout(registry, T0)
    registry.update_markout_horizon(markout_id, 0, 0.55)
    registry.update_markout_reference(markout_id, 0, 0.55, 0.05)

    # Act
    data = kpi_mod.report(db_path=registry.db_path, run_id="all")

    # Assert — both figures present, both on the same fill, and the baseline
    # is the whole difference between them.
    assert data["markout_samples"] == 1
    assert data["adverse_selection"] == pytest.approx(0.15)
    assert data["markout_excess_samples"] == 1
    assert data["adverse_selection_excess"] == pytest.approx(0.10)


def test_a_markout_without_a_baseline_is_left_out_of_the_excess(registry, tmp_path, monkeypatch):
    # Arrange — only rows that actually carry a peer baseline can separate a
    # market-wide move from our own adverse selection.
    from core_brain import kpi as kpi_mod

    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    markout_id = _seed_markout(registry, T0)
    registry.update_markout_horizon(markout_id, 0, 0.55)

    # Act
    data = kpi_mod.report(db_path=registry.db_path, run_id="all")

    # Assert
    assert data["markout_excess_samples"] == 0
    assert data["adverse_selection_excess"] is None


def test_the_peer_baseline_is_unsigned_like_the_raw_drift(registry, monkeypatch):
    # Arrange — a SELL fill. `kpi.report` computes the raw drift as
    # `reference - fill_price` with no side factor, so signing the peer figure
    # and not the raw one would flip the excess on every sell.
    fill_ts = T0 - 4000
    con = sqlite3.connect(str(registry.db_path))
    con.execute(
        "INSERT INTO markouts (ts, condition_id, side, token_id, fill_price,"
        " size, done, run_id) VALUES (?,?,'SELL','tok-up',?,?,0,'run-1')",
        (fill_ts, "0xmarket", 0.40, 200.0),
    )
    con.commit()
    markout_id = con.execute("SELECT MAX(id) FROM markouts").fetchone()[0]
    con.close()

    class _Market:
        up_token = "tok-up"
        down_token = "tok-down"

    monkeypatch.setattr("core_brain.markets.fetch_pinned_market",
                        lambda *a, **k: _Market())
    monkeypatch.setattr("core_brain.markets.full_book",
                        lambda host, token: {"best_bid": 0.55, "best_ask": 0.57})

    trades = [_print(fill_ts, 0.50, 100.0), _print(fill_ts + 300, 0.55, 100.0)]

    # Act
    sample_pending_markouts(registry, now_sec=T0, trades_fn=lambda token, cid=None: trades)

    # Assert — +0.05, the same sign the raw drift carries for this fill.
    assert _refs(registry, markout_id)["h0"]["peer"] == pytest.approx(0.05)


def test_a_window_still_open_is_left_for_a_later_pass(registry, monkeypatch):
    # Arrange — the reference window is centred on the horizon, so at the
    # moment the horizon comes due half of it is still in the future.
    # Measuring then would build the VWAP from the first half and store it as
    # though it covered the whole window.
    fill_ts = T0 - 300.0  # the 300s horizon comes due exactly now
    markout_id = _seed_markout(registry, fill_ts)

    class _Market:
        up_token = "tok-up"
        down_token = "tok-down"

    monkeypatch.setattr("core_brain.markets.fetch_pinned_market",
                        lambda *a, **k: _Market())
    monkeypatch.setattr("core_brain.markets.full_book",
                        lambda host, token: {"best_bid": 0.55, "best_ask": 0.57})

    trades = [_print(fill_ts + 300, 0.55, 100.0)]

    # Act
    sample_pending_markouts(registry, now_sec=T0,
                            trades_fn=lambda token, cid=None: trades)

    # Assert — the mid is recorded, the reference is not yet.
    assert _refs(registry, markout_id) == {}


def test_the_default_tape_reader_keeps_only_this_token(monkeypatch):
    # Arrange — both legs of a market trade in the same feed, at mirror
    # prices. Averaging them would price the reference at something neither
    # leg ever printed at.
    from core_brain import markout as markout_mod

    page = [
        {"asset": "tok-up", "timestamp": T0, "price": 0.40, "size": 100.0},
        {"asset": "tok-down", "timestamp": T0, "price": 0.60, "size": 100.0},
    ]
    captured: list[dict] = []

    class _Resp:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def _urlopen(req, timeout=None):
        captured.append({"url": req.full_url})
        return _Resp(page)

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)

    # Act
    fetch = markout_mod._default_trades_fn()
    rows = fetch("tok-up", "0xmarket")

    # Assert — queried by condition, filtered to the token.
    assert "condition=0xmarket" in captured[0]["url"]
    assert "market=0xmarket" not in captured[0]["url"]
    assert [r["asset"] for r in rows] == ["tok-up"]


def test_the_default_tape_reader_v2_envelope_with_cursor(monkeypatch):
    import json
    import time
    from core_brain import markout as markout_mod

    captured = []
    now = time.time()

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def _urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        captured.append({"url": url, "timeout": timeout})
        if "cursor=" not in url:
            return _Resp({"data": [{"token_id": "tok-up", "price": 0.5, "timestamp": now}], "pagination": {"next_cursor": "c2"}})
        return _Resp({"data": [{"token_id": "tok-up", "price": 0.51, "timestamp": now}], "pagination": {"next_cursor": None}})

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)

    fetch = markout_mod._default_trades_fn()
    rows = fetch("tok-up", "0xmarket")
    assert len(rows) == 2
    assert "condition=0xmarket" in captured[0]["url"]
    assert "cursor=c2" in captured[1]["url"]


def test_the_default_tape_reader_needs_a_market():
    # Arrange — the endpoint is queried by condition id; without one there is
    # nothing to ask for, and a token-only guess would return the whole feed.
    from core_brain import markout as markout_mod

    # Act / Assert
    assert markout_mod._default_trades_fn()("tok-up", None) == []

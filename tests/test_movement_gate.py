"""Recent movement is measured and ENFORCED before quoting (#74).

24h volume says a market traded SOMETIME. It cannot say whether anything is
happening now: the shadow run sat 4.3 hours on a market at 0.23 with 1,777
shares ahead and zero traded, inside every existing bar the whole time, tying
up resting capital that was never going to fill.

The gate shipped RECORD-ONLY (`select_min_movement_usd` = 0.0) so the bar could
be chosen from the recorded `movement_usd` column. The unified-universe redesign
(2026-09-08) turned it into a real gate: $200 of traded notional per 30-minute
window (#416 widened it from $500 for live-sports point swings), measured
BEFORE the two book fetches -- a dead tape costs one request,
not three. Unmeasured tape stays fail-open: None proceeds and is recorded,
never treated as flat.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts.filter_markets import _cause, evaluate, movement_reject, traded_notional

NOW = 1_788_000_000.0
WINDOW = 900.0


class _TapeSession:
    """A session that answers the trades endpoint with a canned payload."""

    def __init__(self, payload, boom: bool = False):
        self.payload = payload
        self.boom = boom
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.boom:
            raise OSError("tape unreachable")
        payload = self.payload

        class _Resp:
            def json(self_inner):
                return payload

        return _Resp()


def _trade(ts: float, price: float, size: float) -> dict:
    return {"timestamp": ts, "price": price, "size": size}


def test_only_trades_inside_the_window_are_counted():
    # Arrange — one print inside the 15m window, one well outside it.
    session = _TapeSession([_trade(NOW - 60, 0.50, 100.0),
                            _trade(NOW - 4000, 0.50, 900.0)])

    # Act
    measured = traded_notional(session, "0xmarket", window_sec=WINDOW, now_ts=NOW)

    # Assert — $50 inside, the older $450 ignored.
    assert measured == pytest.approx(50.0)


def test_a_flat_market_measures_zero_rather_than_unknown():
    # Arrange — the tape answered; nothing traded in the window.
    session = _TapeSession([_trade(NOW - 7200, 0.23, 500.0)])

    # Act
    measured = traded_notional(session, "0xflat", window_sec=WINDOW, now_ts=NOW)

    # Assert — zero is a measurement, and it is the one the gate acts on.
    assert measured == 0.0


def test_an_unreadable_tape_is_unmeasured_not_flat():
    # Arrange
    failed = _TapeSession(None, boom=True)
    wrong_shape = _TapeSession({"error": "nope"})

    # Act / Assert — None, never 0.0: a failed HTTP call must not read as
    # "this market is dead".
    assert traded_notional(failed, "0xmarket", now_ts=NOW) is None
    assert traded_notional(wrong_shape, "0xmarket", now_ts=NOW) is None


def test_garbage_rows_are_skipped_not_fatal():
    # Arrange
    session = _TapeSession([_trade(NOW - 30, 0.50, 100.0),
                            {"timestamp": "nope", "price": "x", "size": None},
                            "not-a-row"])

    # Act
    measured = traded_notional(session, "0xmarket", window_sec=WINDOW, now_ts=NOW)

    # Assert
    assert measured == pytest.approx(50.0)


def test_the_gate_is_inert_at_the_shipped_record_only_bar():
    # Arrange / Act / Assert — a stone-dead market is still admitted while the
    # bar is 0.0, because the column exists to CHOOSE the bar.
    assert movement_reject(0.0, min_movement_usd=0.0) == (False, "")


def test_a_flat_market_is_refused_once_a_bar_is_set():
    # Arrange / Act
    flagged, reason = movement_reject(0.0, min_movement_usd=100.0, window_sec=WINDOW)

    # Assert
    assert flagged is True
    assert "no movement" in reason
    assert "flat" in reason
    assert "15m" in reason


def test_a_moving_market_clears_the_bar():
    # Arrange / Act
    flagged, reason = movement_reject(250.0, min_movement_usd=100.0)

    # Assert
    assert flagged is False
    assert reason == ""


def test_an_unmeasured_market_is_never_refused_for_movement():
    # Arrange — the tape read failed; refusing here would empty the universe
    # on one bad minute at the venue.
    flagged, _ = movement_reject(None, min_movement_usd=100.0)

    # Assert
    assert flagged is False


def test_movement_rejections_bucket_as_one_gate():
    # Arrange — the reason carries the measured dollars, so raw text would
    # make one dashboard card per market.
    reasons = ["no movement: $0 traded in last 15m under $100 (flat)",
               "no movement: $12 traded in last 15m under $100 (flat)"]

    # Act / Assert
    assert {_cause(r) for r in reasons} == {"no movement"}


# --- the gate inside evaluate ---------------------------------------------

class _MarketSession:
    """Books for both tokens plus the tape, on one session."""

    def __init__(self, tape):
        self.tape = tape
        self.tape_calls = 0

    def get(self, url, params=None, timeout=None):
        payload: object
        if "trades" in url:
            self.tape_calls += 1
            payload = self.tape
        else:
            payload = {
                "bids": [{"price": "0.49", "size": "5000"},
                         {"price": "0.48", "size": "5000"}],
                "asks": [{"price": "0.51", "size": "5000"},
                         {"price": "0.52", "size": "5000"}],
            }

        class _Resp:
            def json(self_inner):
                return payload

        return _Resp()


class _UnmeasuredTapeSession:
    """A tape that always fails, and books that always answer."""

    def get(self, url, params=None, timeout=None):
        if "trades" in url:
            raise OSError("tape unreachable")
        payload = {
            "bids": [{"price": "0.49", "size": "5000"}],
            "asks": [{"price": "0.51", "size": "5000"}],
        }

        class _Resp:
            def json(self_inner):
                return payload

        return _Resp()


def _candidate() -> dict:
    return {
        "condition_id": "0xliquid",
        "question": "Will BTC close above 100k?",
        "market_slug": "btc-100k",
        "category": "Crypto",
        "market_type": "",
        "market_group": "",
        "series_title": "Bitcoin",
        "event_title": "Bitcoin price",
        "tokens": [{"token_id": "111"}, {"token_id": "222"}],
        "rewards": {"max_spread": 3.5, "min_size": 50},
        "minimum_tick_size": 0.01,
        # Two days out: inside the horizon gate, so the movement gate is the
        # one under test rather than the one that fires first.
        "end_date_iso": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "_order_min": 5,
        "_spread": 0.02,
        # The resolution state every real gamma_universe row carries; the
        # horizon gate reads it (#312).
        "closed": False,
        "accepting_orders": True,
    }


def test_evaluate_records_the_measured_movement_on_every_row():
    # Arrange — a market that is trading right now, above the shipped bar.
    import time as _time
    session = _MarketSession([_trade(_time.time() - 30, 0.50, 2000.0)])

    # Act
    row = evaluate(session, 5.0, _candidate(), volume_24h=250_000.0, source="spread")

    # Assert — measured and carried, so a bar can be chosen from evidence.
    assert row is not None
    assert row["movement_usd"] == pytest.approx(1000.0)
    assert row["movement_window_sec"] == 1800.0
    assert session.tape_calls == 1


def test_evaluate_refuses_a_flat_market_once_the_bar_is_set(monkeypatch):
    # Arrange — nothing has traded in the window, and enforcement is on.
    import scripts.filter_markets as fm
    monkeypatch.setattr(fm, "MIN_MOVEMENT_USD", 100.0)
    session = _MarketSession([_trade(1.0, 0.23, 500.0)])

    # Act
    row = evaluate(session, 5.0, _candidate(), volume_24h=250_000.0, source="spread")

    # Assert
    assert row is not None
    assert row["eligible"] is False
    assert "no movement" in row["reject_reason"]


def test_evaluate_refuses_a_flat_market_at_the_shipped_bar():
    # Arrange — nothing has traded in the window; no monkeypatch, the shipped
    # $200/30m bar (#416) does the refusing on its own now.
    session = _MarketSession([_trade(1.0, 0.23, 500.0)])

    # Act
    row = evaluate(session, 5.0, _candidate(), volume_24h=250_000.0, source="spread")

    # Assert
    assert row is not None
    assert row["eligible"] is False
    assert "no movement" in row["reject_reason"]


def test_evaluate_uses_the_injected_movement_bar():
    session = _MarketSession([_trade(1.0, 0.23, 500.0)])

    row = evaluate(session, 5.0, _candidate(), volume_24h=250_000.0,
                   source="spread", min_movement_usd=0.0)

    assert row["eligible"] is True


def test_score_pool_forwards_the_movement_bar(monkeypatch):
    from scripts import filter_markets as fm

    seen = {}

    def spy(session, rate, market, volume_24h=None, source="spread", **kwargs):
        seen.update(kwargs)
        return {"eligible": False, "reject_reason": "test"}

    monkeypatch.setattr(fm, "evaluate", spy)
    fm.score_pool([(1.0, _candidate(), 250_000.0, "spread")],
                  session_factory=lambda: object(), max_workers=1,
                  min_movement_usd=123.0)

    assert seen["min_movement_usd"] == 123.0


def test_an_unmeasured_tape_never_refuses_a_liquid_market():
    # Arrange — the tape read fails; the books answer fine. Refusing here
    # would empty the universe on one bad minute at the venue, so the row
    # proceeds with the unmeasured reading recorded.
    session = _UnmeasuredTapeSession()

    # Act
    row = evaluate(session, 5.0, _candidate(), volume_24h=250_000.0, source="spread")

    # Assert — eligible, with None carried as the honest movement figure.
    assert row is not None
    assert row["eligible"] is True
    assert row["movement_usd"] is None


def test_non_finite_and_negative_prints_are_skipped():
    # Arrange — venue garbage: `inf` would make any market look active, and a
    # negative print would subtract real activity from the window.
    session = _TapeSession([_trade(NOW - 30, 0.50, 100.0),
                            _trade(NOW - 30, float("inf"), 10.0),
                            _trade(NOW - 30, float("nan"), 10.0),
                            _trade(NOW - 30, -0.50, 100.0),
                            _trade(NOW - 30, 0.50, -100.0)])

    # Act
    measured = traded_notional(session, "0xmarket", window_sec=WINDOW, now_ts=NOW)

    # Assert — only the one good print counts.
    assert measured == pytest.approx(50.0)


def test_a_normal_live_sports_swing_clears_the_widened_bar():
    # #416 — a 5c-8c point swing on modest size (~$275/30m) is ordinary live
    # play, not a stall: it clears the $200 bar with margin to spare.
    flagged, reason = movement_reject(275.0, min_movement_usd=200.0)
    assert flagged is False
    assert reason == ""


def test_a_quiet_but_alive_book_still_clears_the_widened_bar():
    # $200 refuses multi-hour stalls ($0), not quiet-but-alive books.
    flagged, _ = movement_reject(200.0, min_movement_usd=200.0)
    assert flagged is False


def test_a_near_dead_tape_is_still_refused_at_the_widened_bar():
    # The widening is not a removal: $12/30m is no market at all.
    flagged, reason = movement_reject(12.0, min_movement_usd=200.0)
    assert flagged is True
    assert "no movement" in reason


def test_evaluate_admits_a_normal_sports_swing_at_shipped_bars():
    # End to end at the shipped defaults: prices swinging 0.52-0.58 (6c)
    # on ~$275/30m of prints is admitted, not refused for movement.
    import time as _time
    t_now = _time.time()
    tape = [_trade(t_now - 60 - 60 * i, 0.52 + 0.01 * (i % 7), 50.0)
            for i in range(10)]
    session = _MarketSession(tape)

    row = evaluate(session, 5.0, _candidate(), volume_24h=250_000.0,
                   source="spread")

    assert row is not None
    assert row["eligible"] is True
    assert "no movement" not in row.get("reject_reason", "")


def test_non_finite_env_overrides_are_refused(monkeypatch):
    # Arrange — `inf` would refuse every market on earth; `nan` compares false
    # against everything and silently disables the gate.
    from scoring.config import load

    monkeypatch.setenv("HUNTER_MIN_MOVEMENT_USD", "inf")
    monkeypatch.setenv("HUNTER_MOVEMENT_WINDOW_SEC", "nan")

    # Act
    cfg = load()

    # Assert — the shipped defaults stand (now enforced, per the redesign;
    # movement bar widened to $200 by #416 for live-sports point swings).
    assert cfg.select_min_movement_usd == 200.0
    assert cfg.select_movement_window_sec == 1800.0

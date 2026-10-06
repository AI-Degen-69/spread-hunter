"""Pre-start sports markets are kept out of the quoting universe (#73).

A sports market opens for trading before the event begins and sits flat with
zero tape until the first serve: the shadow run stalled 4.3 hours on a tennis
market at 0.23 with 1,777 shares ahead and nothing traded. The [0.20, 0.80] mid
gate cannot catch it — a pre-start 0.45/0.55 is inside the band — so the
scheduled start time is its own gate, applied before the book fetches.
"""
from __future__ import annotations

import pytest

from scripts.filter_markets import _cause, evaluate, market_start_iso, pre_start
from core_brain.markets import (
    IN_PLAY_WINDOW_SEC,
    LiveMarket,
    quote_t_remaining,
)

NOW = "2026-08-31T12:00:00Z"


class _ExplodingSession:
    """Any book fetch here means the gate ran too late to save the work."""

    def get(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("evaluate fetched a book for a pre-start market")


def _market(start_iso: str | None) -> dict:
    return {
        "condition_id": "0xtennis",
        "question": "Will Vilius Gaubas win?",
        "market_slug": "gaubas-win",
        "category": "Sports",
        "market_type": "",
        "market_group": "",
        "series_title": "ATP",
        "event_title": "ATP Winston-Salem",
        "tokens": [{"token_id": "111"}, {"token_id": "222"}],
        "rewards": {"max_spread": 3.5, "min_size": 50},
        "_start_iso": start_iso,
    }


def test_a_future_start_time_is_pre_start():
    # Arrange / Act
    flagged, reason = pre_start("2026-08-31T15:30:00Z", now_iso=NOW)

    # Assert
    assert flagged is True
    assert "pre-start" in reason
    assert "3.5h" in reason


def test_a_started_event_is_not_pre_start():
    # Arrange / Act
    flagged, reason = pre_start("2026-08-31T11:00:00Z", now_iso=NOW)

    # Assert
    assert flagged is False
    assert reason == ""


def test_an_unknown_start_time_is_not_pre_start():
    # Arrange — most markets state no start time; refusing them all would
    # empty the universe on a missing field.
    assert pre_start(None, now_iso=NOW) == (False, "")
    assert pre_start("", now_iso=NOW) == (False, "")
    assert pre_start("not-a-date", now_iso=NOW) == (False, "")


def test_the_start_time_is_read_from_the_venue_shapes_that_carry_it():
    # Arrange
    direct = {"gameStartTime": "2026-08-31T15:30:00Z"}
    nested = {"events": [{"gameStartTime": "2026-08-31T16:00:00Z"}]}
    listing_only = {"startDate": "2026-08-20T09:00:00Z"}

    # Act / Assert — `startDate` is the listing date, not the event's start.
    assert market_start_iso(direct) == "2026-08-31T15:30:00Z"
    assert market_start_iso(nested) == "2026-08-31T16:00:00Z"
    assert market_start_iso(listing_only) is None


def test_evaluate_rejects_a_pre_start_market_before_fetching_its_books():
    # Arrange — a start time far enough out that the assertion does not depend
    # on when the suite runs.
    market = _market("2099-01-01T00:00:00Z")

    # Act
    row = evaluate(_ExplodingSession(), 0.0, market,
                   volume_24h=25000.0, source="spread")

    # Assert
    assert row is not None
    assert row["eligible"] is False
    assert "pre-start" in row["reject_reason"]
    assert row["cid"] == "0xtennis"


def test_the_pre_start_reason_buckets_as_one_gate():
    # Arrange — the reason carries a countdown, so raw text would make one
    # dashboard card per market.
    reasons = ["pre-start: event has not started (starts in 3.5h)",
               "pre-start: event has not started (starts in 42m)"]

    # Act / Assert
    assert {_cause(r) for r in reasons} == {"pre-start"}


# --- quote-time kickoff clock (#386) ----------------------------------------
# The venue endDate of a sports/esports market is kickoff, so the quote-time
# countdown goes negative the moment the match starts. Fixed vectors from the
# live FURIA vs Aurora market, 2026-10-06.

_FURIA_END = 1791244800.0      # 2026-10-06T00:00:00Z
_FURIA_KICKOFF = 1791301200.0  # 2026-10-06T15:40:00Z
_FURIA_NOW = 1791304481.0      # 2026-10-06T16:34:41Z, old countdown -59681s


def _furia_market(end_ts=_FURIA_END, kickoff=_FURIA_KICKOFF):
    return LiveMarket(
        condition_id="0xfuria", market_slug="cs2-furia-aur1-2026-10-06",
        up_token="U", down_token="D", start_ts=_FURIA_END - 3600.0,
        end_ts=end_ts, tick_size=0.01, neg_risk=False,
        game_start_ts=kickoff)


def test_quote_clock_in_play_furia_extends_past_kickoff_end_date():
    market = _furia_market()
    assert market.t_remaining(_FURIA_NOW) == -59681.0
    got = quote_t_remaining(market, now=_FURIA_NOW)
    assert got == _FURIA_KICKOFF + IN_PLAY_WINDOW_SEC - _FURIA_NOW
    assert got > 0


def test_quote_clock_pre_start_stays_negative():
    market = _furia_market(kickoff=_FURIA_NOW + 58 * 60.0)
    assert quote_t_remaining(market, now=_FURIA_NOW) == -59681.0


def test_quote_clock_genuine_expiry_without_kickoff_stays_negative():
    market = _furia_market(kickoff=None)
    assert quote_t_remaining(market, now=_FURIA_NOW) == -59681.0


def test_quote_clock_stale_match_after_window_is_negative():
    market = _furia_market(
        kickoff=_FURIA_NOW - IN_PLAY_WINDOW_SEC - 60.0)
    assert quote_t_remaining(market, now=_FURIA_NOW) < 0


def test_quote_clock_keeps_real_later_end():
    market = _furia_market(end_ts=_FURIA_NOW + 7 * 86400.0,
                           kickoff=_FURIA_NOW - 1800.0)
    assert quote_t_remaining(market, now=_FURIA_NOW) == 7 * 86400.0


def test_quote_clock_stand_in_without_kickoff_field():
    class _Bare:
        def t_remaining(self):
            return 3600.0

    assert quote_t_remaining(_Bare()) == 3600.0


def test_fetch_pinned_market_reads_game_start_time(monkeypatch):
    from core_brain import markets as markets_mod

    payload = {
        "rewards": {"rates": []},
        "closed": False,
        "accepting_orders": True,
        "tokens": [{"token_id": "U"}, {"token_id": "D"}],
        "end_date_iso": "2026-10-06T00:00:00Z",
        "game_start_time": "2026-10-06T15:40:00Z",
        "market_slug": "cs2-furia-aur1-2026-10-06",
        "minimum_tick_size": 0.01,
        "neg_risk": False,
    }

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(markets_mod._SESSION, "get",
                        lambda *a, **k: _Resp())
    market = markets_mod.fetch_pinned_market("0xfuria",
                                             require_rewards=False)
    assert market.game_start_ts == _FURIA_KICKOFF
    assert market.end_ts == _FURIA_END

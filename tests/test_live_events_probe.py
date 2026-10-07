"""The live-events fetch and main-line selection, against a recorded contract.

Fixed vectors from the live venue, 2026-10-06. The endpoint finding is in
`docs/polymarket-live-events-endpoint.md`: `GET /events?live=true&closed=false`
returns the live set directly, so live markets are fetched rather than hunted
by paginating the volume-sorted listing.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts import live_events_probe as probe

# --- recorded venue rows ----------------------------------------------------
# `lol-est-kbm`: live LoL event, match level 2-0, game 3 in progress.
# Its 41 markets carry exactly one `moneyline` (the match winner) and four
# `child_moneyline` (per-game). The main line must be found on the TYPE, not on
# an absent group label -- every market on an LoL event has a group label.
_ESTRAL_EVENT = {
    "slug": "lol-est-kbm-2026-10-06",
    "live": True,
    "ended": False,
    "period": "3/5",
    "score": "000-000|0-2|Bo5",
    "sport": {"id": 39, "sport": "lol", "name": "LoL"},
    "markets": [
        {"slug": "lol-est-kbm-2026-10-06", "sportsMarketType": "moneyline",
         "groupItemTitle": "Match Winner", "outcomePrices": '["0.5465", "0.4535"]',
         "bestBid": 0.54, "bestAsk": 0.557, "volume24hr": 59250},
        {"slug": "lol-est-kbm-2026-10-06-game3",
         "sportsMarketType": "child_moneyline",
         "groupItemTitle": "Game 3 Winner",
         "outcomePrices": '["0.885", "0.115"]', "bestBid": 0.88,
         "bestAsk": 0.89, "volume24hr": 15141},
        {"slug": "lol-est-kbm-2026-10-06-game4",
         "sportsMarketType": "child_moneyline",
         "groupItemTitle": "Game 4 Winner",
         "outcomePrices": '["0.755", "0.245"]', "bestBid": 0.58,
         "bestAsk": 0.85, "volume24hr": 864},
        {"slug": "lol-est-kbm-2026-10-06-total-games-3pt5",
         "sportsMarketType": "totals", "groupItemTitle": "O/U 3.5 Games",
         "outcomePrices": '["0.88", "0.12"]', "bestBid": 0.78,
         "bestAsk": 0.85, "volume24hr": 3850},
    ],
}

# The event the operator said to DROP: in play, but decided (15-5, 2-0).
_CPD_FUE_EVENT = {
    "slug": "lol-cpd-fue-2026-10-06",
    "live": True,
    "ended": False,
    "period": "3/5",
    "score": "15-5|2-0|Bo5",
    "sport": {"id": 39, "sport": "lol", "name": "LoL"},
    "markets": [
        {"slug": "lol-cpd-fue-2026-10-06", "sportsMarketType": "moneyline",
         "groupItemTitle": "Match Winner",
         "outcomePrices": '["0.9955", "0.0045"]', "bestBid": 0.994,
         "bestAsk": 0.997, "volume24hr": 34701},
    ],
}

# A live NHL match: one `moneyline` plus submarkets on the same event.
_NHL_EVENT = {
    "slug": "nhl-nsh-tor-2026-10-06", "live": True, "ended": False,
    "period": "End P1", "score": "2-0", "sport": {"sport": "nhl"},
    "markets": [
        {"slug": "nhl-nsh-tor-2026-10-06", "sportsMarketType": "moneyline",
         "groupItemTitle": None, "outcomePrices": '["0.715", "0.285"]',
         "bestBid": 0.71, "bestAsk": 0.72, "volume24hr": 63664,
         "gameStartTime": "2026-10-06 23:00:00+00"},
        {"slug": "nhl-nsh-tor-2026-10-06-total-5pt5",
         "sportsMarketType": "totals", "groupItemTitle": "O/U 5.5",
         "outcomePrices": '["0.5", "0.5"]', "bestBid": 0.49,
         "bestAsk": 0.51, "volume24hr": 9000},
    ],
}

# THE REAL-SPORTS TRAP. Unlike esports, a live MLB game is split into its own
# top-level EVENTS per submarket. Each carries no `moneyline`, so the type
# selector must not admit them as if they were the match winner.
_MLB_INNING_EVENT = {
    "slug": "mlb-lad-atl-2026-10-06-inning-5-winner", "live": True,
    "ended": False, "period": "Bot 5th", "score": "3-0",
    "sport": {"sport": "mlb"},
    "markets": [
        {"slug": "mlb-lad-atl-2026-10-06-inning-5-winner-dodgers",
         "sportsMarketType": "baseball_team_inning5_winner",
         "groupItemTitle": "Los Angeles Dodgers",
         "outcomePrices": '["0.06", "0.94"]', "bestBid": None,
         "bestAsk": 0.12, "volume24hr": 0},
    ],
}

_MLB_EVENT = {
    "slug": "mlb-lad-atl-2026-10-06", "live": True, "ended": False,
    "period": "Bot 5th", "score": "3-0", "sport": {"sport": "mlb"},
    "markets": [
        {"slug": "mlb-lad-atl-2026-10-06", "sportsMarketType": "moneyline",
         "groupItemTitle": None, "outcomePrices": '["0.84", "0.16"]',
         "bestBid": 0.83, "bestAsk": 0.85, "volume24hr": 1122921,
         "gameStartTime": "2026-10-06 22:00:00+00"},
    ],
}

# Soccer is 3-way: three separate binary markets on one event.
_SOCCER_EVENT = {
    "slug": "bra2-goi-ath-2026-10-06", "live": True, "ended": False,
    "period": "1H", "score": "0-0", "sport": {"sport": "bra2"},
    "markets": [
        {"slug": "bra2-goi-ath-2026-10-06-home",
         "sportsMarketType": "moneyline", "groupItemTitle": "Goias EC",
         "outcomePrices": '["0.505", "0.495"]', "bestBid": 0.5,
         "bestAsk": 0.51, "volume24hr": 234531},
        {"slug": "bra2-goi-ath-2026-10-06-draw",
         "sportsMarketType": "moneyline", "groupItemTitle": "Draw",
         "outcomePrices": '["0.315", "0.685"]', "bestBid": 0.31,
         "bestAsk": 0.32, "volume24hr": 403},
        {"slug": "bra2-goi-ath-2026-10-06-away",
         "sportsMarketType": "moneyline", "groupItemTitle": "Athletic Club",
         "outcomePrices": '["0.185", "0.815"]', "bestBid": 0.18,
         "bestAsk": 0.19, "volume24hr": 8979},
    ],
}

_SOCCER_PSEUDO_EVENT = {
    "slug": "conl-atg-aru-2026-10-06-second-half-result", "live": True,
    "ended": False, "period": "1H", "score": "2-1",
    "sport": {"sport": "conl"},
    "markets": [
        {"slug": "conl-atg-aru-2026-10-06-second-half-result-aru",
         "sportsMarketType": "soccer_second_half_result",
         "groupItemTitle": "Aruba",
         "outcomePrices": '["0.345", "0.655"]', "bestBid": 0.29,
         "bestAsk": 0.4, "volume24hr": 0},
    ],
}

# Tennis: flagged live while the stated kickoff is 11 hours in the FUTURE,
# with a score showing a match already in progress. The venue's clock is not
# reliable for multi-day tournaments.
_TENNIS_EVENT = {
    "slug": "wta-buyukak-klimovi-2026-10-05", "live": True, "ended": False,
    "period": "S3", "score": "6-3, 6-7(2-7), 0-3", "sport": {"sport": "wta"},
    "markets": [
        {"slug": "wta-buyukak-klimovi-2026-10-05",
         "sportsMarketType": "moneyline", "groupItemTitle": None,
         "outcomePrices": '["0.095", "0.905"]', "bestBid": 0.09,
         "bestAsk": 0.1, "volume24hr": 348643,
         "gameStartTime": "2026-10-07 11:00:00+00"},
    ],
}

_FINISHED_ESPORTS_EVENT = {
    "slug": "cs2-fal2-navi-2026-10-06", "live": False, "ended": True,
    "period": "3/3", "score": "000-000|2-1|Bo3",
    "sport": {"sport": "cs2"},
    "markets": [{"slug": "cs2-fal2-navi-2026-10-06",
                 "sportsMarketType": "moneyline", "groupItemTitle": "Match Winner",
                 "outcomePrices": '["0.9995", "0.0005"]', "bestBid": 0.999,
                 "bestAsk": 1.0, "volume24hr": 4987220}],
}

_ALL = [_ESTRAL_EVENT, _CPD_FUE_EVENT, _NHL_EVENT, _MLB_EVENT,
        _MLB_INNING_EVENT, _SOCCER_EVENT, _SOCCER_PSEUDO_EVENT,
        _TENNIS_EVENT, _FINISHED_ESPORTS_EVENT]


# --- main-line selection ----------------------------------------------------


def test_the_main_line_is_selected_by_market_type_not_by_group_label():
    # Arrange / Act: every market on the event has a group label, so selecting
    # on an ABSENT label would find nothing.
    rows = probe.main_lines([_ESTRAL_EVENT])

    # Assert
    assert len(rows) == 1
    assert rows[0]["market"]["slug"] == "lol-est-kbm-2026-10-06"
    assert rows[0]["market"]["sportsMarketType"] == "moneyline"


def test_the_per_game_moneylines_are_not_the_main_line():
    # Arrange / Act
    rows = probe.main_lines([_ESTRAL_EVENT])

    # Assert: `child_moneyline` is "Game N Winner", a different market.
    slugs = [r["market"]["slug"] for r in rows]
    assert "lol-est-kbm-2026-10-06-game3" not in slugs
    assert "lol-est-kbm-2026-10-06-game4" not in slugs


def test_totals_and_other_submarkets_are_excluded():
    # Arrange / Act
    rows = probe.main_lines([_ESTRAL_EVENT])

    # Assert
    types = {r["market"]["sportsMarketType"] for r in rows}
    assert types == {"moneyline"}


def test_a_real_sports_match_winner_is_selected():
    # Arrange / Act
    rows = probe.main_lines([_MLB_EVENT])

    # Assert
    assert [r["market"]["slug"] for r in rows] == ["mlb-lad-atl-2026-10-06"]


def test_a_real_sports_per_inning_pseudo_event_is_not_a_main_line():
    # Arrange: real sports expose each submarket group as its OWN top-level
    # event, so an inning-winner event looks like a live MLB event. It carries
    # `baseball_team_inning5_winner`, not `moneyline`.
    # Act
    rows = probe.main_lines([_MLB_INNING_EVENT])

    # Assert
    assert rows == [], "an inning-winner event must not read as the match winner"


def test_a_soccer_per_half_pseudo_event_is_not_a_main_line():
    # Arrange / Act
    rows = probe.main_lines([_SOCCER_PSEUDO_EVENT])

    # Assert
    assert rows == []


def test_soccer_exposes_all_three_ways():
    # Arrange / Act: soccer moneylines are Home/Draw/Away, not two-way.
    rows = probe.main_lines([_SOCCER_EVENT])

    # Assert: three separate binary markets, all main lines.
    assert len(rows) == 3
    assert {r["market"]["groupItemTitle"] for r in rows} == {
        "Goias EC", "Draw", "Athletic Club"}


def test_only_the_live_main_lines_survive_the_selection():
    # Arrange / Act
    rows = probe.main_lines(_ALL)

    # Assert: every live main line is kept -- across sports -- and the
    # finished CS2 event is not.
    assert sorted({r["event_slug"] for r in rows}) == [
        "bra2-goi-ath-2026-10-06",
        "lol-cpd-fue-2026-10-06",
        "lol-est-kbm-2026-10-06",
        "mlb-lad-atl-2026-10-06",
        "nhl-nsh-tor-2026-10-06",
        "wta-buyukak-klimovi-2026-10-05",
    ]


def test_a_finished_event_is_excluded_even_when_the_fetch_widened():
    # Arrange / Act
    rows = probe.main_lines([_FINISHED_ESPORTS_EVENT])

    # Assert: `live` false and `ended` true, so nothing is admitted.
    assert rows == []


# --- sport filtering --------------------------------------------------------


def test_a_sport_filter_excludes_every_other_sport():
    # Arrange / Act
    rows = probe.main_lines(_ALL, sports={"nhl"})

    # Assert
    assert [r["sport"] for r in rows] == ["nhl"]


def test_a_filter_matching_nothing_returns_nothing():
    # Arrange / Act: ping-pong is not a venue sport.
    rows = probe.main_lines(_ALL, sports={"table_tennis"})

    # Assert
    assert rows == []


def test_no_filter_means_every_sport():
    # Arrange / Act
    rows = probe.main_lines(_ALL)

    # Assert
    assert {"nhl", "mlb", "lol", "bra2", "wta"} <= {r["sport"] for r in rows}


# --- event context ----------------------------------------------------------


def test_event_context_travels_with_the_market():
    # Arrange -- the period and score are what tell a reader the match is live
    # and how far along.
    rows = probe.main_lines([_ESTRAL_EVENT])

    # Assert
    assert rows[0]["period"] == "3/5"
    assert rows[0]["score"] == "000-000|0-2|Bo5"
    assert rows[0]["live"] is True


def test_ways_counts_a_three_way_soccer_line_as_three_markets():
    # Arrange / Act
    counts = probe.ways(probe.main_lines([_SOCCER_EVENT, _NHL_EVENT]))

    # Assert: counting EVENTS would understate the tradeable population.
    assert counts["bra2-goi-ath-2026-10-06"] == 3
    assert counts["nhl-nsh-tor-2026-10-06"] == 1


# --- the clock, and the live flag disagreeing with it -----------------------


def test_the_venue_kickoff_format_is_parsed():
    # Arrange: space-separated, hour-only offset -- not `T` and not `Z`.
    now = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)

    # Act
    elapsed = probe.elapsed_hours("2026-10-06 23:00:00+00", now)

    # Assert
    assert elapsed == 1.0


def test_a_live_event_with_a_future_kickoff_reads_negative():
    # Arrange: the tennis anomaly -- flagged live with a match in progress,
    # but the stated kickoff is hours ahead.
    now = datetime(2026, 10, 6, 23, 53, tzinfo=timezone.utc)

    # Act
    elapsed = probe.elapsed_hours("2026-10-07 11:00:00+00", now)

    # Assert: the disagreement is reported, not hidden.
    assert elapsed is not None and elapsed < 0


def test_an_absent_or_unparsable_clock_is_unknown_not_zero():
    # Arrange / Act / Assert
    assert probe.elapsed_hours(None, datetime.now(timezone.utc)) is None
    assert probe.elapsed_hours("not a date", datetime.now(timezone.utc)) is None


# --- the decided-market filter ----------------------------------------------


def test_a_decided_live_match_is_recognised_by_price():
    # Arrange: in play but 15-5 up in the series -- the match is over.
    m = _CPD_FUE_EVENT["markets"][0]

    # Act / Assert
    assert max(probe._prices(m)) >= probe.FINISHED_PRICE


def test_a_competitive_live_match_is_not_decided():
    # Arrange
    m = _ESTRAL_EVENT["markets"][0]

    # Act / Assert
    assert max(probe._prices(m)) < probe.FINISHED_PRICE


def test_the_estral_market_clears_the_spread_bar():
    # Arrange: both sides quoted inside MAX_SPREAD is what makes a pair
    # tradeable at all.
    m = _ESTRAL_EVENT["markets"][0]

    # Act
    spread = probe._spread(m)

    # Assert
    assert spread is not None
    assert spread <= probe.MAX_SPREAD


def test_a_wall_of_submarkets_has_no_two_sided_book():
    # Arrange: bid absent, ask present -- one-sided, not tradeable.
    m = {"bestBid": None, "bestAsk": 0.001}

    # Act / Assert
    assert probe._spread(m) is None, "a one-sided book must not read as a spread"


def test_unparsable_prices_do_not_raise():
    # Arrange: the venue has served non-JSON here before.
    m = {"outcomePrices": "not json"}

    # Act / Assert
    assert probe._prices(m) == []


def test_real_live_estral_volume_now_clears_the_permanent_floor():
    # Arrange: the floor is $50,000 and this market measured $48,486 earlier in
    # the evening. By the time the direct fetch replaced pagination it had
    # traded up, which is what a live match does.
    m = _ESTRAL_EVENT["markets"][0]

    # Act / Assert
    assert float(m["volume24hr"]) >= 50_000.0


# --- the fetch itself --------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    """Records the request so the live filter can be asserted on the wire."""

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params") or {}))
        return _FakeResponse(self.payload)


def test_the_fetch_asks_the_venue_for_live_not_closed_events():
    # Arrange
    session = _FakeSession([_ESTRAL_EVENT])

    # Act
    probe.live_events(session)

    # Assert: `closed=false` is required -- `live=true` alone also returns
    # long-finished events.
    url, params = session.calls[0]
    assert url == probe.EVENTS
    assert params.get("live") == "true"
    assert params.get("closed") == "false"


def test_a_full_page_is_followed_to_the_next_one():
    # Arrange: a full page means the live set was cut off, not exhausted.
    class _Paged:
        def __init__(self):
            self.offsets: list[int] = []

        def get(self, url, **kwargs):
            offset = kwargs["params"].get("offset") or 0
            self.offsets.append(offset)
            # Page 1 is full, page 2 is short -- so the walk stops there.
            n = probe.LIVE_EVENT_PAGE if offset == 0 else 1
            return _FakeResponse([_NHL_EVENT] * n)

    session = _Paged()

    # Act
    events = probe.live_events(session)

    # Assert: advanced by what the ENDPOINT returned, and stopped on a short
    # page rather than reporting a truncated set as a complete one.
    assert session.offsets == [0, probe.LIVE_EVENT_PAGE]
    assert len(events) == probe.LIVE_EVENT_PAGE + 1


def test_a_short_first_page_stops_after_one_request():
    # Arrange: the usual case -- the live set fits in one page.
    session = _FakeSession([_NHL_EVENT])

    # Act
    events = probe.live_events(session)

    # Assert: one request, not a speculative second.
    assert len(session.calls) == 1
    assert len(events) == 1


def test_a_wrapped_payload_yields_its_events():
    # Arrange: the endpoint has served a `{"data": [...]}` wrapper. Reporting
    # those events as "no live main line" is the one outcome a probe must not
    # produce.
    session = _FakeSession({"data": [_ESTRAL_EVENT]})

    # Act
    events = probe.live_events(session)

    # Assert
    assert [e["slug"] for e in events] == [_ESTRAL_EVENT["slug"]]


def test_a_wrapper_round_an_empty_list_is_an_empty_live_set():
    # Arrange: the venue answered and nothing is live. That is NOT an error.
    # Act / Assert
    assert probe.live_events(_FakeSession({"data": []})) == []


def test_a_payload_that_is_not_events_is_an_error_not_an_empty_set():
    # Arrange: the venue answered, but not with events.
    # Act / Assert: reading this as "nothing is live" would print a quiet
    # night over a response the probe never understood.
    with pytest.raises(ValueError):
        probe.live_events(_FakeSession({"error": "rate limited"}))


def test_an_unreadable_live_set_is_reported_and_exits_non_zero(
        monkeypatch, capsys):
    # Arrange
    monkeypatch.setattr(probe.requests, "Session",
                        lambda: _FakeSession({"error": "rate limited"}))

    # Act
    rc = probe.main([])

    # Assert
    out = capsys.readouterr().out
    assert rc == 1
    assert "unreadable" in out


def test_a_transport_error_is_reported_not_raised(monkeypatch, capsys):
    # Arrange: a real transport failure is a `requests` error, which is the
    # only class this fetch catches.
    class _Boom:
        def get(self, *a, **k):
            raise probe.requests.ConnectionError("connection reset")

    monkeypatch.setattr(probe.requests, "Session", lambda: _Boom())

    # Act
    rc = probe.main([])

    # Assert: the probe reports and exits non-zero rather than crashing.
    assert rc == 1
    assert "unreachable" in capsys.readouterr().out


def test_an_unexpected_error_is_not_relabelled_as_a_venue_outage(monkeypatch):
    # Arrange: a bug in this file must not read as "the venue is down".
    class _Buggy:
        def get(self, *a, **k):
            raise TypeError("a bug, not an outage")

    monkeypatch.setattr(probe.requests, "Session", lambda: _Buggy())

    # Act / Assert
    with pytest.raises(TypeError):
        probe.main([])


# --- the printed verdict -----------------------------------------------------
# The verdict is what a reader acts on, so it is asserted rather than left to
# the eye. Each case pins one arm of the KEEP/DROP decision.


def _report(monkeypatch, events) -> None:
    """Run the probe against a fixed live set, for its printed verdicts."""
    monkeypatch.setattr(probe.requests, "Session",
                        lambda: _FakeSession(events))
    assert probe.main([]) == 0


def test_a_decided_live_match_is_dropped(monkeypatch, capsys):
    # Arrange: in play but 15-5 up in the series -- over as a contest.
    # Act
    _report(monkeypatch, [_CPD_FUE_EVENT])

    # Assert
    out = capsys.readouterr().out
    assert "DROP decided" in out
    assert "lol-cpd-fue" in out


def test_a_live_market_with_no_two_sided_book_is_dropped(monkeypatch, capsys):
    # Arrange: bid absent, ask present -- a wall of submarkets.
    event = {**_ESTRAL_EVENT, "markets": [
        {**_ESTRAL_EVENT["markets"][0], "bestBid": None}]}

    # Act
    _report(monkeypatch, [event])

    # Assert
    assert "DROP no two-sided book" in capsys.readouterr().out


def test_a_wide_book_is_dropped_with_its_measured_spread(monkeypatch, capsys):
    # Arrange: both sides quoted, but too far apart to pair.
    event = {**_ESTRAL_EVENT, "markets": [
        {**_ESTRAL_EVENT["markets"][0], "bestBid": 0.40, "bestAsk": 0.50}]}

    # Act
    _report(monkeypatch, [event])

    # Assert
    assert "DROP spread 0.100" in capsys.readouterr().out


def test_a_healthy_live_main_line_is_kept(monkeypatch, capsys):
    # Arrange / Act
    _report(monkeypatch, [_ESTRAL_EVENT])

    # Assert
    assert "KEEP" in capsys.readouterr().out


def test_a_live_event_with_a_future_kickoff_is_kept_with_a_clock_note(
        monkeypatch, capsys):
    # Arrange: the tennis anomaly -- flagged live, match in progress, kickoff
    # hours ahead. The flag and the clock disagree; the probe reports that and
    # does NOT resolve it by dropping the market. The clock is frozen: the
    # recorded 11:00 UTC kickoff only reads "future" against a now before it,
    # and wall-clock time must not decide this test.
    class _FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 6, 23, 53, tzinfo=timezone.utc)

    monkeypatch.setattr(probe, "datetime", _FrozenClock)
    # Act
    _report(monkeypatch, [_TENNIS_EVENT])

    # Assert
    out = capsys.readouterr().out
    assert "KEEP" in out, "the clock must not decide tradeability"
    assert "DROP" not in out
    assert "CLOCK DISAGREES" in out


def test_the_clock_disagreement_never_appears_as_a_drop_reason(
        monkeypatch, capsys):
    # Arrange: same market, so the note rides beside a KEEP verdict.
    # Act
    _report(monkeypatch, [_TENNIS_EVENT])

    # Assert: a `DROP ... clock ...` line would decide the question the note
    # exists to leave open.
    assert "DROP clock" not in capsys.readouterr().out


def test_the_sport_flag_restricts_the_report(monkeypatch, capsys):
    # Arrange
    monkeypatch.setattr(probe.requests, "Session",
                        lambda: _FakeSession([_NHL_EVENT, _ESTRAL_EVENT]))

    # Act
    rc = probe.main(["--sport", "nhl"])

    # Assert
    out = capsys.readouterr().out
    assert rc == 0
    assert "nhl-nsh-tor-2026-10-06" in out
    assert "lol-est-kbm-2026-10-06" not in out

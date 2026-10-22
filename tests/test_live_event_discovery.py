"""Live-event discovery: fetch the live set, merge it, and never double-count.

`GET /events?live=true&closed=false` is the discovery path for in-play markets.
The scan cannot provide it: in-play sub-floor rows are spread 8-16 per page
across every page of the volume-sorted listing, so no bounded page budget
reliably reaches a named match.

Vectors are recorded from the live venue, 2026-10-06/07.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts import filter_markets as fm

# --- recorded venue rows ----------------------------------------------------
# A live NHL match: one `moneyline` plus submarkets on the same event. The
# nested market carries NO `category` and NO `events` key of its own, which is
# why the row builder must read identity from the parent event.
_NHL_EVENT = {
    "id": "7001", "slug": "nhl-nsh-tor-2026-10-06",
    "title": "Predators vs. Maple Leafs",
    "live": True, "ended": False, "period": "End P1", "score": "2-0",
    "sport": {"id": 35, "sport": "nhl", "name": "NHL"},
    "tags": [{"label": "Sports"}, {"label": "NHL"}, {"label": "Hockey"}],
    "series": [{"id": "10346", "slug": "nhl-2026", "title": "NHL 2026"}],
    "markets": [
        {"conditionId": "0xnhlmain", "slug": "nhl-nsh-tor-2026-10-06",
         "question": "Predators vs. Maple Leafs",
         "sportsMarketType": "moneyline", "groupItemTitle": None,
         "clobTokenIds": '["111", "222"]',
         "outcomePrices": '["0.715", "0.285"]', "bestBid": 0.71,
         "bestAsk": 0.72, "spread": 0.01, "volume24hr": 63664.9,
         "gameStartTime": "2026-10-06 23:00:00+00",
         "endDate": "2026-10-06T23:00:00Z",
         "enableOrderBook": True, "acceptingOrders": True, "closed": False,
         "orderMinSize": 5, "orderPriceMinTickSize": 0.01,
         "rewardsMaxSpread": 4.5, "rewardsMinSize": 50},
        {"conditionId": "0xnhlou", "slug": "nhl-nsh-tor-2026-10-06-ou-5pt5",
         "question": "Predators vs. Maple Leafs O/U 5.5",
         "sportsMarketType": "totals", "groupItemTitle": "O/U 5.5",
         "clobTokenIds": '["333", "444"]', "spread": 0.02,
         "volume24hr": 9000, "bestBid": 0.49, "bestAsk": 0.51,
         "gameStartTime": "2026-10-06 23:00:00+00",
         "enableOrderBook": True, "acceptingOrders": True, "closed": False},
    ],
}

# THE REAL-SPORTS TRAP: a live MLB game is split into its own top-level EVENTS
# per submarket group, each carrying the same live flag, sport, period and
# score as the real game -- and no `moneyline`.
_MLB_INNING_EVENT = {
    "id": "7002", "slug": "mlb-lad-atl-2026-10-06-inning-5-winner",
    "title": "Dodgers vs. Braves",
    "live": True, "ended": False, "period": "Bot 5th", "score": "3-0",
    "sport": {"sport": "mlb", "name": "MLB"},
    "series": [{"slug": "mlb", "title": "MLB"}],
    "markets": [
        {"conditionId": "0xinning5", "slug": "mlb-lad-atl-inning-5-dodgers",
         "question": "Will the Dodgers win inning 5?",
         "sportsMarketType": "baseball_team_inning5_winner",
         "groupItemTitle": "Los Angeles Dodgers",
         "clobTokenIds": '["555", "666"]', "spread": 0.06,
         "volume24hr": 0, "bestBid": None, "bestAsk": 0.12,
         "enableOrderBook": True, "acceptingOrders": True, "closed": False},
    ],
}

# Tennis: flagged live while the stated kickoff sits 11 hours in the FUTURE,
# with a score showing a match already in progress.
_TENNIS_EVENT = {
    "id": "7003", "slug": "wta-buyukak-klimovi-2026-10-05",
    "title": "Samsun: Cagla Buyukakcay vs Linda Klimovicova",
    "live": True, "ended": False, "period": "S3",
    "score": "6-3, 6-7(2-7), 0-3",
    "sport": {"sport": "wta", "name": "WTA Tour"},
    "series": [{"slug": "wta", "title": "WTA"}],
    "markets": [
        {"conditionId": "0xwta", "slug": "wta-buyukak-klimovi-2026-10-05",
         "question": "Samsun: Cagla Buyukakcay vs Linda Klimovicova",
         "sportsMarketType": "moneyline", "groupItemTitle": None,
         "clobTokenIds": '["777", "888"]', "spread": 0.01,
         "volume24hr": 348643.0, "bestBid": 0.09, "bestAsk": 0.1,
         "gameStartTime": "2026-10-07 11:00:00+00",
         "endDate": "2026-10-07T11:00:00Z",
         "enableOrderBook": True, "acceptingOrders": True, "closed": False,
         "orderMinSize": 5, "orderPriceMinTickSize": 0.01},
    ],
}

_FINISHED_EVENT = {
    "id": "7004", "slug": "cs2-fal2-navi-2026-10-06",
    "title": "Falcons vs. Natus Vincere",
    "live": False, "ended": True, "period": "3/3", "score": "2-1",
    "sport": {"sport": "cs2", "name": "CS2"},
    "series": [{"slug": "esl-pro-league", "title": "ESL Pro League"}],
    "markets": [
        {"conditionId": "0xdone", "slug": "cs2-fal2-navi-2026-10-06",
         "question": "Falcons vs. Natus Vincere",
         "sportsMarketType": "moneyline", "clobTokenIds": '["999", "000"]',
         "spread": 0.001, "volume24hr": 4987220.0, "closed": True,
         "acceptingOrders": False},
    ],
}

_ALL = [_NHL_EVENT, _MLB_INNING_EVENT, _TENNIS_EVENT, _FINISHED_EVENT]


class _NoNetwork:
    """Any venue call is a bug in the test, not a path under test."""

    def get(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the venue was reached")


class _FakeResponse:
    def __init__(self, payload, *, boom=False):
        self._payload = payload
        self._boom = boom

    def raise_for_status(self):
        if self._boom:
            raise RuntimeError("503 from the venue")

    def json(self):
        return self._payload


class _FakeSession:
    """Serves one payload and records the request it was asked for."""

    def __init__(self, payload, *, boom=False):
        self.payload = payload
        self.boom = boom
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((str(url), kwargs.get("params") or {}))
        return _FakeResponse(self.payload, boom=self.boom)


class _PagedSession:
    """Serves offset windows of one live set, the way the venue pages it.

    `fail_from` makes every request at or past that offset fail, which is the
    later-page timeout the fetch has to survive.
    """

    def __init__(self, events, *, fail_from=None):
        self.events = events
        self.fail_from = fail_from
        self.offsets: list[int] = []

    def get(self, url, **kwargs):
        params = kwargs.get("params") or {}
        offset = int(params.get("offset") or 0)
        self.offsets.append(offset)
        if self.fail_from is not None and offset >= self.fail_from:
            return _FakeResponse(None, boom=True)
        return _FakeResponse(self.events[offset:offset + int(params["limit"])])


def _live_page(count, start=0):
    """`count` distinct live NHL main lines, one per event."""
    out = []
    for i in range(start, start + count):
        marker = f"0xnhlmain{i}"
        market = dict(_NHL_EVENT["markets"][0], conditionId=marker)
        out.append(dict(_NHL_EVENT, id=f"7{i:04d}", markets=[market]))
    return out


# --- the fetch --------------------------------------------------------------


def test_the_fetch_asks_for_live_not_closed_events():
    # Arrange
    session = _FakeSession([_NHL_EVENT])

    # Act
    fm.live_event_markets(session)

    # Assert: `closed=false` is required -- `live=true` alone also returns
    # long-finished events.
    url, params = session.calls[0]
    assert url == fm.LIVE_EVENTS
    assert params["live"] == "true"
    assert params["closed"] == "false"
    # Nested markets inherit the event's tags, so the event page must ask for
    # them or every live row arrives with none (#748).
    assert params["include_tag"] == "true"


def test_only_the_main_line_is_taken_from_a_live_event():
    # Arrange / Act
    rows, stats = fm.live_event_markets(_FakeSession([_NHL_EVENT]))

    # Assert: the O/U submarket on the same event is not the match winner.
    assert [r["market_slug"] for r in rows] == ["nhl-nsh-tor-2026-10-06"]
    assert stats["live_events"] == 1
    assert stats["live_main_lines"] == 1


def test_a_per_inning_pseudo_event_is_never_a_live_main_line():
    # Arrange: real sports expose each submarket group as its own top-level
    # event, carrying the same live flag as the real game.
    # Act
    rows, stats = fm.live_event_markets(_FakeSession([_MLB_INNING_EVENT]))

    # Assert: `baseball_team_inning5_winner` is not `moneyline`, so the
    # pseudo-event contributes no market -- and is not counted as a live game.
    assert rows == []
    assert stats["live_main_lines"] == 0


def test_a_finished_event_is_not_iterated_even_when_the_fetch_widened():
    # Arrange: the query string filters server-side, but a finished match must
    # be refused on the payload too -- it is the shape that reads as a
    # tradeable book while being pure settlement.
    # Act
    rows, stats = fm.live_event_markets(_FakeSession([_FINISHED_EVENT]))

    # Assert
    assert rows == []
    assert stats["live_events"] == 0


def test_a_dict_wrapper_is_unwrapped_when_it_carries_data():
    # Arrange: the endpoint has served a dict wrapper before.
    # Act
    rows, stats = fm.live_event_markets(_FakeSession({"data": [_NHL_EVENT]}))

    # Assert: unwrapped, not discarded -- and not reported as a failure.
    assert [r["condition_id"] for r in rows] == ["0xnhlmain"]
    assert stats["live_error"] is None


def test_a_payload_that_carries_no_events_is_reported():
    # Arrange: neither a list nor a wrapper -- nothing here is an event, and
    # silently returning none would read as a night with no live game.
    # Act
    rows, stats = fm.live_event_markets(_FakeSession({"error": "rate limited"}))

    # Assert
    assert rows == []
    assert stats["live_error"]


def test_a_transport_failure_is_reported_not_raised():
    # Arrange: the rank must not fail because the live set was unreadable.
    session = _FakeSession(None, boom=True)

    # Act
    rows, stats = fm.live_event_markets(session)

    # Assert: reported as that, rather than as a night with no live game.
    assert rows == []
    assert "503" in (stats["live_error"] or "")


def test_every_live_row_is_stamped_as_the_admission_signal():
    # Arrange / Act
    rows, _ = fm.live_event_markets(_FakeSession([_NHL_EVENT]))

    # Assert: this stamp is what skips the clock gates in `evaluate` and picks
    # the live volume bar in `tradable`.
    assert rows[0]["_live_event"] is True


# --- the row's identity fields ---------------------------------------------


def test_a_live_market_under_the_live_bar_is_counted_not_emitted():
    # Arrange: $2,680 against a $10,000 live bar -- as a real ATP main line
    # measured. `tradable` would refuse it anyway; emitting it first would buy
    # a tape read and two book fetches for a refusal the bar already implies.
    event = {**_NHL_EVENT, "markets": [
        {**_NHL_EVENT["markets"][0], "volume24hr": 2_680.0}]}

    # Act
    rows, stats = fm.live_event_markets(_FakeSession([event]))

    # Assert: counted, so the bar stays tunable from evidence.
    assert rows == []
    assert stats["live_below_bar"] == 1


def test_the_live_bar_admits_a_market_the_permanent_floor_would_hide():
    # Arrange: $40,000 -- under the $50,000 permanent floor, over the live bar.
    # This is the whole point of the lower bar.
    event = {**_NHL_EVENT, "markets": [
        {**_NHL_EVENT["markets"][0], "volume24hr": 40_000.0}]}

    # Act
    rows, stats = fm.live_event_markets(_FakeSession([event]))

    # Assert
    assert [r["condition_id"] for r in rows] == ["0xnhlmain"]
    assert stats["live_below_bar"] == 0
    ok, why = fm.tradable(40_000.0, 0.2, "Predators vs. Maple Leafs",
                          "nhl-nsh-tor-2026-10-06", "", "", "", "NHL 2026",
                          "Predators vs. Maple Leafs", min_volume_usd=50_000.0,
                          live_event=True)
    assert ok is True, why


def test_the_series_title_comes_from_the_parent_event():
    # Arrange: the nested market carries no category and no series, so a row
    # built from the market alone is refused by the identity gate no matter
    # how good the market is.
    rows, _ = fm.live_event_markets(_FakeSession([_NHL_EVENT]))

    # Assert
    assert rows[0]["series_title"] == "NHL 2026"
    assert rows[0]["event_title"] == "Predators vs. Maple Leafs"
    assert rows[0]["event_slug"] == "nhl-nsh-tor-2026-10-06"


def test_the_market_level_category_is_left_blank():
    # Arrange: the identity gate reads `category`, so event-level enrichment
    # must never land in it -- a venue category on a market that has none would
    # admit or reject markets on a field the venue never set.
    rows, _ = fm.live_event_markets(_FakeSession([_NHL_EVENT]))

    # Assert
    assert rows[0]["category"] == ""
    assert rows[0]["venue_category"] == "NHL", "the display label is separate"


def test_a_live_nhl_main_line_clears_the_identity_gate():
    # Arrange: the end-to-end property that matters -- the row this module
    # builds is one the funnel will actually consider.
    rows, _ = fm.live_event_markets(_FakeSession([_NHL_EVENT]))
    r = rows[0]

    # Act
    ok, why = fm.identity_allowed(
        r["question"], r["market_slug"], r["category"], r["market_type"],
        r["market_group"], r["series_title"], r["event_title"])

    # Assert
    assert ok is True, why


def test_a_live_market_carries_the_event_evidence_on_its_row():
    # Arrange / Act
    rows, _ = fm.live_event_markets(_FakeSession([_NHL_EVENT]))

    # Assert: a reader can see WHY the row is live rather than trusting a flag.
    assert rows[0]["_sport"] == "nhl"
    assert rows[0]["_event_period"] == "End P1"
    assert rows[0]["_event_score"] == "2-0"


def test_a_live_row_has_exactly_two_tokens():
    # Arrange: nothing downstream can quote a non-binary market.
    rows, _ = fm.live_event_markets(_FakeSession([_NHL_EVENT]))

    # Assert
    assert [t["token_id"] for t in rows[0]["tokens"]] == ["111", "222"]


def test_a_market_without_a_condition_id_is_dropped():
    # Arrange: a half-built row reads as a market with a missing field rather
    # than as an unreadable one.
    event = {**_NHL_EVENT,
             "markets": [{**_NHL_EVENT["markets"][0], "conditionId": None}]}

    # Act
    rows, _ = fm.live_event_markets(_FakeSession([event]))

    # Assert
    assert rows == []


# --- pagination (#744) -------------------------------------------------------


def test_the_fetch_pages_through_a_slate_bigger_than_one_page():
    # Arrange: the venue caps a page at 100 and ignores a bigger `limit`, so a
    # single request reported a 250-market slate as a 100-market one.
    events = _live_page(250)
    session = _PagedSession(events)

    # Act
    rows, stats = fm.live_event_markets(session)

    # Assert
    assert len(rows) == 250
    assert session.offsets == [0, fm.LIVE_EVENT_PAGE, 2 * fm.LIVE_EVENT_PAGE]
    assert stats["live_pages"] == 3
    assert stats["live_events"] == 250
    assert stats["live_main_lines"] == 250
    assert stats["live_truncated"] is False
    assert stats["live_error"] is None


def test_the_page_cap_reports_the_live_set_as_truncated():
    # Arrange: a slate larger than the page budget must not read as complete.
    session = _PagedSession(_live_page(fm.LIVE_EVENT_MAX_PAGES * fm.LIVE_EVENT_PAGE + 5))

    # Act
    rows, stats = fm.live_event_markets(session)

    # Assert
    assert len(session.offsets) == fm.LIVE_EVENT_MAX_PAGES
    assert len(rows) == fm.LIVE_EVENT_MAX_PAGES * fm.LIVE_EVENT_PAGE
    assert stats["live_truncated"] is True


def test_a_later_page_failure_keeps_the_pages_already_read():
    # Arrange: page 2 times out. The 100 live markets page 1 returned are real.
    session = _PagedSession(_live_page(250), fail_from=fm.LIVE_EVENT_PAGE)

    # Act
    rows, stats = fm.live_event_markets(session)

    # Assert
    assert len(rows) == fm.LIVE_EVENT_PAGE
    assert stats["live_pages"] == 1
    assert stats["live_error"]
    assert stats["live_truncated"] is False


# --- the lane decision -------------------------------------------------------


def test_market_lane_reads_the_stamp_first_and_flags_second():
    from scoring import selector

    # A row stamped this cycle is never re-guessed: the stamp is the lane.
    assert selector.market_lane({"lane": "live"}) == "live"
    assert selector.market_lane({"lane": "regular", "_live_event": True}) == "regular"
    # The pre-#744 artifact fields, which is how the WTA row in the 2026-10-21
    # `markets.json` (and any older file) is still classified correctly.
    assert selector.market_lane({"_event_live": True}) == "live"
    assert selector.market_lane({"event_live": True}) == "live"
    assert selector.market_lane({"_live_event": True}) == "live"
    assert selector.market_lane({"volatility_exempt": True}) == "live"
    # Strictly True, never truthy: a string flag is not a venue declaration.
    assert selector.market_lane({"event_live": "true"}) == "regular"
    assert selector.market_lane({"lane": "unknown"}) == "regular"
    assert selector.market_lane({}) == "regular"
    assert selector.market_lane(None) == "regular"


# --- the merge --------------------------------------------------------------


def test_the_live_row_wins_over_the_scanned_copy_of_the_same_market():
    # Arrange: a live market above the volume floor is ALSO in the paginated
    # scan. Keying it the other way would drop the `_live_event` stamp on
    # exactly the markets it exists for.
    scanned = [{"condition_id": "0xnhlmain", "market_slug": "from-the-scan"}]

    # Act
    merged, stats = fm.merge_live_event_markets(
        scanned, _FakeSession([_NHL_EVENT]))

    # Assert
    assert len(merged) == 1
    assert merged[0]["market_slug"] == "nhl-nsh-tor-2026-10-06"
    assert merged[0]["_live_event"] is True
    assert stats["live_rows_already_scanned"] == 1
    assert stats["live_rows_merged"] == 0


def test_a_market_the_scan_did_not_find_is_appended_once():
    # Arrange: an empty scan, as a sub-floor live market would produce.
    # Act
    merged, stats = fm.merge_live_event_markets([], _FakeSession([_NHL_EVENT]))

    # Assert
    assert [r["condition_id"] for r in merged] == ["0xnhlmain"]
    assert stats["live_rows_merged"] == 1


def test_the_merge_does_not_double_count_a_market():
    # Arrange: one live main line across two events is still one market.
    events = [_NHL_EVENT, _NHL_EVENT]

    # Act
    merged, _ = fm.merge_live_event_markets([], _FakeSession(events))

    # Assert: the funnel must not count the same book twice.
    assert len(merged) == 1


def test_the_merge_leaves_the_scanned_universe_untouched():
    # Arrange
    scanned = [{"condition_id": "0xother", "market_slug": "btc-above-100k"}]

    # Act
    merged, _ = fm.merge_live_event_markets(scanned, _FakeSession([_NHL_EVENT]))

    # Assert: appended, never rewritten in place.
    assert scanned[0]["market_slug"] == "btc-above-100k"
    assert [r["condition_id"] for r in merged] == ["0xother", "0xnhlmain"]


def test_an_unreachable_live_set_leaves_the_universe_intact():
    # Arrange: the live fetch failing must not cost the rank its scan.
    scanned = [{"condition_id": "0xother"}]

    # Act
    merged, stats = fm.merge_live_event_markets(
        scanned, _FakeSession(None, boom=True))

    # Assert
    assert merged == scanned
    # Nothing was declared live, so the lane is decided all the same: a ranker
    # row without a lane is what used to make the dashboard guess.
    assert merged[0]["lane"] == "regular"
    assert stats["live_error"]
    assert stats["live_rows_merged"] == 0


def test_every_merged_row_carries_a_truncation_flag():
    # Arrange: the scan stamps its own rows; the live rows are appended after
    # it, and a row without the flag reads as "this listing was not cut off".
    # Act
    merged, _ = fm.merge_live_event_markets([], _FakeSession([_NHL_EVENT]))

    # Assert
    assert merged[0]["fetch_truncated"] is False


def test_a_match_that_kicked_off_moves_lane_at_unchanged_length():
    # Arrange: the scan admitted this row before kickoff, so it carries the
    # lane the last cycle gave it. The venue now declares the same market live.
    scanned = [{"condition_id": "0xnhlmain",
                "market_slug": "nhl-nsh-tor-2026-10-06", "lane": "regular"}]

    # Act
    merged, stats = fm.merge_live_event_markets(scanned, _FakeSession([_NHL_EVENT]))

    # Assert: re-derived on this cycle, at unchanged length (never scored twice).
    assert len(merged) == 1
    assert merged[0]["lane"] == "live"
    assert merged[0]["_live_event"] is True
    assert stats["live_rows_already_scanned"] == 1


def test_an_undeclared_row_is_stamped_regular_on_every_merge():
    # Arrange: a scanned row that already carries a stale live stamp.
    scanned = [{"condition_id": "0xbtc", "lane": "live", "_live_event": True}]

    # Act
    merged, _ = fm.merge_live_event_markets(scanned, _FakeSession([_NHL_EVENT]))

    # Assert: an old stamp must not survive; the venue did not declare it live.
    assert [r["lane"] for r in merged] == ["regular", "live"]


def test_a_declared_in_play_market_that_was_not_admitted_is_still_live():
    # Arrange: the MLB inning submarket is in a live game but is not a main
    # line, so the moneyline selector refuses to ADMIT it. That is a refusal to
    # quote, not a statement that the match is not being played.
    scanned = [{"condition_id": "0xinning5"}]

    # Act
    merged, stats = fm.merge_live_event_markets(
        scanned, _FakeSession([_MLB_INNING_EVENT]))

    # Assert
    row = merged[0]
    assert row["lane"] == "live"
    assert row["_event_live"] is True
    assert row["_sport"] == "mlb"
    assert row["sports_market_type"] == "baseball_team_inning5_winner"
    # The lane grants nothing: no admission flag leaks in.
    assert "_live_event" not in row
    assert "volatility_exempt" not in row
    assert stats["live_rows_declared_only"] == 1
    assert stats["live_rows_merged"] == 0


def test_a_sub_floor_live_market_is_declared_but_never_admitted():
    # Arrange: the live volume bar is a DISCOVERY bar -- a market under it is
    # not fetched at all. It is still in a match being played right now.
    quiet = dict(_NHL_EVENT["markets"][0], volume24hr=12.0)
    event = dict(_NHL_EVENT, id="7009", markets=[quiet])

    # Act
    merged, stats = fm.merge_live_event_markets(
        [{"condition_id": "0xnhlmain"}], _FakeSession([event]))

    # Assert
    row = merged[0]
    assert stats["live_below_bar"] == 1
    assert row["lane"] == "live"
    assert "_live_event" not in row
    assert stats["live_rows_declared_only"] == 1


# --- the lane on every exit (#744) -------------------------------------------


def _regular_market(days: int = 5) -> dict:
    """A non-sports market that can reach the book gate on its own merits."""
    end = datetime.now(timezone.utc) + timedelta(days=days)
    return {
        "condition_id": "0xpolitics",
        "question": "Will the incumbent win the general election?",
        "market_slug": "incumbent-election",
        "category": "Politics",
        "end_date_iso": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tokens": [{"token_id": "aa"}, {"token_id": "bb"}],
        "closed": False, "accepting_orders": True,
        "minimum_tick_size": 0.01, "_order_min": 5,
        "_volume_24h": 300_000.0, "_spread": 0.02,
    }


class _BookSession:
    """Answers both book legs at mid 0.50 with the given spread."""

    def __init__(self, spread: float, depth: int = 5000):
        self.spread = spread
        self.depth = depth

    def get(self, url, **kwargs):  # pragma: no cover - always called
        if "clob.polymarket.com/book" not in str(url):
            raise AssertionError(f"unexpected venue call {url}")
        half = self.spread / 2.0
        return _FakeResponse({
            "bids": [{"price": f"{0.50 - half:.4f}", "size": str(self.depth)}],
            "asks": [{"price": f"{0.50 + half:.4f}", "size": str(self.depth)}]})


def _live_row() -> dict:
    return fm.live_event_markets(_FakeSession([_NHL_EVENT]))[0][0]


@pytest.mark.parametrize("lane", ["live", "regular"])
def test_every_evaluate_exit_carries_the_lane(lane, monkeypatch, tmp_path):
    # Arrange: one row per lane, driven through every exit `evaluate` has. A
    # row that leaves without a lane is what let the dashboard guess one.
    market = _live_row() if lane == "live" else _regular_market()
    volume = 63_664.0 if lane == "live" else 300_000.0
    monkeypatch.setattr(fm, "tape_movement_and_range",
                        lambda *a, **k: {"movement_usd": 1_000_000.0,
                                         "range_cents": 5.0,
                                         "measured_at": "t", "trade_count": 100})

    # 1. identity refusal: no matchup, no primary/macro field to admit it.
    faceless = dict(market, question="Alpha vs Beta", market_slug="alpha-beta",
                    category="", market_type="", market_group="",
                    series_title="", event_title="")
    out = fm.evaluate(_NoNetwork(), 0.0, faceless, volume_24h=volume,
                      source="spread")
    assert out["eligible"] is False
    assert out["lane"] == lane

    # 2. movement refusal: the tape is flat.
    monkeypatch.setattr(fm, "tape_movement_and_range",
                        lambda *a, **k: {"movement_usd": 0.0, "range_cents": 0.0,
                                         "measured_at": "t", "trade_count": 0})
    out = fm.evaluate(_NoNetwork(), 0.0, market, volume_24h=volume,
                      source="spread")
    assert out["eligible"] is False
    assert "movement" in out["reject_reason"]
    assert out["lane"] == lane

    # 3. book refusal: a book too wide to quote, with the tape alive.
    monkeypatch.setattr(fm, "tape_movement_and_range",
                        lambda *a, **k: {"movement_usd": 1_000_000.0,
                                         "range_cents": 5.0,
                                         "measured_at": "t", "trade_count": 100})
    out = fm.evaluate(_BookSession(spread=0.10), 0.0, market,
                      volume_24h=volume, source="spread")
    assert out["eligible"] is False
    assert "spread" in out["reject_reason"]
    # The book exit is the one live refusal that used to carry no provenance at
    # all; it now says the market was live as well as which lane it is in.
    if lane == "live":
        assert out["live_event"] is True
        assert out["event_live"] is True
        assert out["sports_market_type"] == "moneyline"
        assert out["volume_bar_usd"] == fm.MIN_VOLUME_24H_LIVE
    assert out["lane"] == lane

    # 4. eligible: a tight, deep book.
    out = fm.evaluate(_BookSession(spread=0.02), 0.0, market,
                      volume_24h=volume, source="spread")
    assert out["eligible"] is True
    assert out["lane"] == lane

    # 5. picked/final are projections of the eligible row, not an exit.
    monkeypatch.setattr(fm, "RUN", tmp_path)
    fm._write_pipeline_snapshot(
        cands=[], spread_cands=[market], out=[out], eligible=[out],
        picked=[out], causes={}, census="", gates="", attempted=1,
        rejected=0, out_dir=tmp_path)
    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))
    assert snap["final"][0]["lane"] == lane
    assert snap["picked"][0]["lane"] == lane


def test_the_snapshot_publishes_per_lane_numbers_that_add_up(tmp_path):
    # Arrange: two lanes, one live refusal, one live pick, one regular refusal,
    # and one live candidate that never got a verdict.
    out = [
        {"source": "spread", "eligible": False,
         "reject_reason": "no movement in window", "volume_24h": 100.0,
         "cid": "0xlive1", "title": "Live one", "slug": "live-1",
         "lane": "live", "days_to_resolve": 0.1},
        {"source": "spread", "eligible": True, "reject_reason": "",
         "volume_24h": 50_000.0, "cid": "0xlive2", "title": "Live two",
         "slug": "live-2", "lane": "live", "days_to_resolve": 0.2},
        {"source": "spread", "eligible": False,
         "reject_reason": "24h volume $10 < $250,000", "volume_24h": 10.0,
         "cid": "0xreg1", "title": "Reg one", "slug": "reg-1",
         "lane": "regular", "days_to_resolve": 3.0},
    ]
    universe = [
        {"condition_id": "0xlive1", "question": "Live one", "lane": "live"},
        {"condition_id": "0xlive2", "question": "Live two", "lane": "live"},
        {"condition_id": "0xlive3", "question": "Live three", "lane": "live"},
        {"condition_id": "0xreg1", "question": "Reg one", "lane": "regular"},
    ]
    causes: dict = {}
    for r in out:
        if not r["eligible"]:
            cause = fm._cause(r["reject_reason"])
            causes[cause] = causes.get(cause, 0) + 1
    eligible = [r for r in out if r["eligible"]]

    # Act
    fm._write_pipeline_snapshot(
        cands=[], spread_cands=universe, out=out, eligible=eligible,
        picked=eligible, causes=causes, census="", gates="",
        attempted=len(universe), rejected=len(out) - len(eligible),
        out_dir=tmp_path)
    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))

    # Assert: the block exists for exactly the two lanes, in the same shape as
    # the global one.
    assert set(snap["lanes"]) == {"live", "regular"}
    global_counts = snap["counts"]
    for key in ("funded", "spread_universe", "attempted", "scored",
                "dropped_no_verdict", "rejected", "eligible", "picked"):
        assert (snap["lanes"]["live"]["counts"][key]
                + snap["lanes"]["regular"]["counts"][key]) == global_counts[key], key
    live = snap["lanes"]["live"]["counts"]
    assert live == {"funded": 0, "spread_universe": 3, "attempted": 3,
                    "scored": 2, "dropped_no_verdict": 1, "rejected": 1,
                    "eligible": 1, "picked": 1}
    # A lane's numbers describe that lane only: nothing live leaks into regular.
    assert snap["lanes"]["regular"]["counts"] == {
        "funded": 0, "spread_universe": 1, "attempted": 1, "scored": 1,
        "dropped_no_verdict": 0, "rejected": 1, "eligible": 0, "picked": 0}
    # Per-lane rejections reuse the existing entry shape exactly.
    assert (set(snap["lanes"]["live"]["rejections"][0])
            == set(snap["rejections"][0])
            == {"cause", "n", "would_fund", "traps", "examples"})
    assert snap["lanes"]["live"]["rejections"][0]["n"] == 1
    assert snap["lanes"]["live"]["rejections"][0]["examples"][0]["lane"] == "live"
    assert snap["lanes"]["regular"]["rejections"][0]["n"] == 1
    # Every preview row says which lane it is previewing.
    assert {r["lane"] for r in snap["raw"]["spread"]} == {"live", "regular"}
    assert snap["final"][0]["lane"] == "live"
    assert snap["picked"][0]["lane"] == "live"


# --- the evidence on the funnel row ----------------------------------------


def test_a_rejected_live_row_says_it_was_live():
    # Arrange: the venue declared it live, but UMA has already proposed a
    # resolution -- so it is refused by a LATER gate. Without the evidence on
    # the row this is indistinguishable from an ordinary refusal.
    row, _ = fm.live_event_markets(_FakeSession([_NHL_EVENT]))
    market = dict(row[0], uma_resolution_status="proposed")

    # Act
    out = fm.evaluate(_NoNetwork(), 0.0, market,
                      volume_24h=63_664.0, source="spread")

    # Assert
    assert out["eligible"] is False
    assert out["live_event"] is True
    assert out["_sport"] == "nhl"
    assert out["event_period"] == "End P1"
    assert out["event_score"] == "2-0"
    # The bar it was actually gated on: comparing `volume_24h` against the
    # permanent floor would read a correct admission as unexplained.
    assert out["volume_bar_usd"] == fm.MIN_VOLUME_24H_LIVE


def test_a_scanned_market_carries_no_live_evidence():
    # Arrange: a market the scan found has no live provenance at all.
    market = {
        "condition_id": "0xscanned", "question": "Will BTC close above 100k?",
        "market_slug": "mkt-btc", "category": "Crypto", "market_type": "",
        "market_group": "", "series_title": "Bitcoin",
        "event_title": "Bitcoin price", "tokens": [{"token_id": "a"}],
        "rewards": {}, "uma_resolution_status": "proposed",
    }

    # Act
    out = fm.evaluate(_NoNetwork(), 0.0, market, volume_24h=250_000.0,
                      source="spread")

    # Assert
    assert "live_event" not in out
    assert "volume_bar_usd" not in out


@pytest.mark.parametrize("sport,expected", [("nhl", True), ("wta", True),
                                          ("mlb", False), ("bra2", False)])
def test_the_stats_name_the_sports_that_were_live(sport, expected):
    # Arrange: `mlb` is present as a pseudo-event only, so it has no main line
    # and must not be reported as a live sport.
    # Act
    _, stats = fm.live_event_markets(_FakeSession(_ALL))

    # Assert
    assert (sport in stats["live_sports"]) is expected


# --- series state (#402 T2) --------------------------------------------------
# A live LoL BO3: FlyQuest vs Shopify Rebellion, one game played. Recorded
# shape per docs/polymarket-live-events-endpoint.md: score carries the running
# game score, the series games, and the best-of suffix.
_BO3_EVENT = {
    "id": "7005", "slug": "lol-fly-sr-2026-10-07",
    "title": "FlyQuest vs Shopify Rebellion",
    "live": True, "ended": False, "period": "2/3", "score": "9-4|1-1|Bo3",
    "sport": {"id": 39, "sport": "lol", "name": "League of Legends"},
    "tags": [{"label": "Esports"}, {"label": "League of Legends"}],
    "series": [{"id": "10400", "slug": "lol-2026", "title": "League of Legends"}],
    "markets": [
        {"conditionId": "0xflysr", "slug": "lol-fly-sr-2026-10-07",
         "question": "FlyQuest vs Shopify Rebellion",
         "sportsMarketType": "moneyline", "groupItemTitle": "Match Winner",
         "clobTokenIds": '["101", "202"]',
         "outcomePrices": '["0.15", "0.85"]', "bestBid": 0.14,
         "bestAsk": 0.16, "spread": 0.02, "volume24hr": 63664.9,
         "gameStartTime": "2026-10-07 15:00:00+00",
         "endDate": "2026-10-07T15:00:00Z",
         "enableOrderBook": True, "acceptingOrders": True, "closed": False,
         "orderMinSize": 5, "orderPriceMinTickSize": 0.01,
         "rewardsMaxSpread": 4.5, "rewardsMinSize": 50},
    ],
}

def test_a_live_row_carries_its_event_evidence():
    rows, _ = fm.live_event_markets(_FakeSession([_BO3_EVENT]))
    row = rows[0]
    assert row["sports_market_type"] == "moneyline"
    assert row["_event_live"] is True
    assert row["_event_ended"] is False
    assert row["_series_ts"] > 0


def test_a_live_row_outside_the_band_is_refused_like_every_other_market(monkeypatch):
    # The live-series mid-gate exemption (#402) fell with the time-of-game
    # purge (#597): a venue-live row whose book sits outside the configured price band is
    # refused as decided, exactly like any other market with the same books.
    # Game progress is not evidence about when this market decides, and the
    # venue's live flag admits the row to the measured gates -- it does not
    # buy it an exemption from the band.
    rows, _ = fm.live_event_markets(_FakeSession([_BO3_EVENT]))
    live_row = rows[0]

    monkeypatch.setattr(
        fm, "tape_movement_and_range",
        lambda *a, **k: {"movement_usd": 1_000_000.0, "range_cents": 5.0,
                         "measured_at": "t", "trade_count": 100})

    def books(url, params=None, timeout=None):
        mid = 0.92 if params["token_id"] == "101" else 0.08
        return _FakeResponse({
            "bids": [{"price": f"{mid - 0.01:.2f}", "size": "5000"}],
            "asks": [{"price": f"{mid + 0.01:.2f}", "size": "5000"}]})

    class _BookSession(_FakeSession):
        def get(self, url, **kwargs):
            if "clob.polymarket.com/book" in str(url):
                return books(url, **kwargs)
            raise AssertionError(f"unexpected venue call {url}")

    out = fm.evaluate(_BookSession([]), 1.0, live_row,
                      volume_24h=63_664.0, source="spread")
    assert "decided mid" in (out.get("reject_reason") or "")

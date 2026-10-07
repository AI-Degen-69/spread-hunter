"""In-play markets: refused by the clock, admitted when the venue says live.

`pre_start` refuses the match that has not begun. This is its mirror: the match
already under way. Without it a started match passes the pre-start and
[0.20, 0.80] mid gates and reaches the depth arm, whose book reading is a
transient in-play book -- thin between rounds, collapsing toward 100c as the
result firms up. That is how the 2026-10-06 CS2 market (Falcons vs Natus
Vincere) was refused as `NO: top-3 bid depth $44.56` when its real defect was
the clock, not liquidity.

Which clock, though, is the question this file now answers. The venue's own
`gameStartTime` contradicts its own `live` flag on multi-day events -- four live
tennis matches measured 2026-10-06 stated a kickoff 10-15 hours in the FUTURE
while their scores showed a match already in progress. So the CLOCK gates keep
refusing what the clock says is under way, and a market from
`GET /events?live=true` is admitted instead: the venue's declaration is the
authority on liveness (operator decision 2026-10-07), and it is what makes the
lower live volume bar reachable rather than dead code.
"""
from __future__ import annotations

from core_brain.markets import IN_PLAY_WINDOW_SEC
from scripts.filter_markets import (
    MIN_VOLUME_24H_LIVE,
    _cause,
    evaluate,
    in_play,
    live_volume_bar,
    tradable,
)

NOW = "2026-10-06T16:34:41Z"   # the live FURIA/Aurora reading, #386


class _ExplodingSession:
    """Any book fetch here means the gate ran too late to save the work."""

    def get(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("evaluate fetched a book for an in-play market")


class _RecordingSession:
    """Records every URL the funnel asks for, and answers nothing.

    A raised exception cannot be used to prove control reached the tape stage:
    `tape_movement_and_range` catches `Exception` and returns unmeasured, so an
    assertion inside `get` is swallowed. Recording the call sites is the only
    way to observe that the gate let the market through.
    """

    def __init__(self):
        self.urls: list[str] = []

    def get(self, url, *args, **kwargs):
        self.urls.append(str(url))
        raise RuntimeError("no network in tests")


def _market(start_iso: str | None, *, live_event: bool = False) -> dict:
    """The CS2 shape: a plain matchup title with a kickoff and no live token."""
    m = {
        "condition_id": "0xcs2",
        "question": "Counter-Strike: Team Falcons vs Natus Vincere (BO3) - "
                    "ESL Pro League Group Stage",
        "market_slug": "cs2-fal2-navi-2026-10-06",
        "category": "Sports",
        "market_type": "",
        "market_group": "",
        "series_title": "ESL Pro League",
        "event_title": "Falcons vs Natus Vincere",
        "tokens": [{"token_id": "111"}, {"token_id": "222"}],
        "rewards": {"max_spread": 3.5, "min_size": 50},
        "_start_iso": start_iso,
    }
    if live_event:
        m["_live_event"] = True
    return m


# --- the clock gate itself --------------------------------------------------


def test_a_started_event_is_in_play():
    # Arrange: kickoff 20m before NOW.
    flagged, reason = in_play("2026-10-06T16:14:41Z", now_iso=NOW)

    # Assert
    assert flagged is True
    assert "in-play" in reason
    assert "20m" in reason


def test_a_still_upcoming_event_is_not_in_play():
    # Arrange: kickoff an hour after NOW; pre_start owns this one.
    assert in_play("2026-10-06T17:34:41Z", now_iso=NOW) == (False, "")


def test_an_unknown_start_time_is_not_in_play():
    # Arrange: most markets state no start time; refusing them all would empty
    # the universe on a missing field, the same contract pre_start carries.
    assert in_play(None, now_iso=NOW) == (False, "")
    assert in_play("", now_iso=NOW) == (False, "")
    assert in_play("not-a-date", now_iso=NOW) == (False, "")


def test_an_event_past_the_window_is_not_in_play():
    # Arrange: an event is presumed finished after its in-play window, so the
    # expiry and horizon gates own it again rather than this one.
    long_ago = "2026-10-06T00:00:00Z"
    assert IN_PLAY_WINDOW_SEC > 0
    assert in_play(long_ago, now_iso=NOW) == (False, "")


def test_a_long_event_reports_hours_not_minutes():
    # Arrange: a BO5 six hours in still reads in hours.
    flagged, reason = in_play("2026-10-06T13:34:41Z", now_iso=NOW)

    # Assert
    assert flagged is True
    assert "3.0h" in reason


def test_naive_venue_times_are_read_as_utc():
    # Arrange: the same aware/naive care days_to_resolve takes.
    flagged, _ = in_play("2026-10-06T16:14:41", now_iso=NOW.replace("Z", ""))

    # Assert
    assert flagged is True


# --- the clock gates in the funnel -----------------------------------------


def test_evaluate_rejects_a_clock_in_play_market_before_fetching_its_books():
    # Arrange: kickoff 20m before the seeded now, and the venue has NOT
    # declared it live -- so the clock still decides, and it refuses.
    market = _market("2026-10-06T16:14:41Z")

    # Act: a session that raises on any fetch. The gate must fire first.
    row = evaluate(_ExplodingSession(), 0.0, market,
                   volume_24h=4_495_025.0, source="spread", now_iso=NOW)

    # Assert
    assert row is not None
    assert row["eligible"] is False
    assert "in-play" in row["reject_reason"]
    assert row["cid"] == "0xcs2"


def test_evaluate_leaves_a_pre_kickoff_market_to_the_pre_start_gate():
    # Arrange: kickoff an hour after the seeded now.
    market = _market("2026-10-06T17:34:41Z")

    # Act
    row = evaluate(_ExplodingSession(), 0.0, market,
                   volume_24h=25000.0, source="spread", now_iso=NOW)

    # Assert: still refused, but by pre-start -- this gate did not fire.
    assert row is not None
    assert "pre-start" in row["reject_reason"]
    assert "in-play" not in row["reject_reason"]


def test_evaluate_does_not_in_play_a_market_with_no_start_time():
    # Arrange: unknown start must not refuse; the funnel moves on to the tape.
    # An exploding session cannot prove that -- `tape_movement_and_range`
    # swallows every fetch failure by design (unmeasured tape stays
    # fail-open), so this records the call sites instead.
    market = _market(None)
    session = _RecordingSession()

    # Act
    evaluate(session, 0.0, market,
             volume_24h=250_000.0, source="spread", now_iso=NOW)

    # Assert: control reached the tape read rather than being refused as
    # in-play, which is the only thing this gate owns.
    assert session.urls, "evaluate refused before the tape stage"
    assert any("trades" in u for u in session.urls)


def test_evaluate_does_not_in_play_a_market_whose_event_finished():
    # Arrange: kickoff well past the in-play window; the expiry/horizon gates
    # own it, so this one stands down and the tape stage is still reached.
    market = _market("2026-10-06T00:00:00Z")
    session = _RecordingSession()

    # Act
    evaluate(session, 0.0, market,
             volume_24h=250_000.0, source="spread", now_iso=NOW)

    # Assert
    assert any("trades" in u for u in session.urls)


# --- the venue's declaration beats the clock --------------------------------


def test_evaluate_admits_a_venue_declared_live_market_past_the_clock_gates():
    # Arrange: kickoff 20m before NOW -- the clock alone would refuse this.
    market = _market("2026-10-06T16:14:41Z", live_event=True)
    session = _RecordingSession()

    # Act
    evaluate(session, 0.0, market,
             volume_24h=60_000.0, source="spread", now_iso=NOW)

    # Assert: the row reached the tape stage instead of being refused on a
    # clock the venue has already overruled.
    assert any("trades" in u for u in session.urls), \
        "a venue-declared live market was refused before the tape stage"


def test_evaluate_admits_a_declared_live_market_whose_clock_is_in_the_future():
    # Arrange: the tennis anomaly -- declared live while its stated kickoff sits
    # hours ahead, with a score showing a match already in progress. A
    # clock-first reading refuses this as "has not started".
    market = _market("2026-10-07T11:00:00Z", live_event=True)
    session = _RecordingSession()

    # Act
    evaluate(session, 0.0, market,
             volume_24h=348_643.0, source="spread", now_iso=NOW)

    # Assert
    assert any("trades" in u for u in session.urls), \
        "the venue's live flag must outrank its own unreliable kickoff time"


def test_a_declared_live_market_still_answers_to_every_later_gate():
    # Arrange: live, but it states no end date and its books cannot be read.
    # The declaration buys passage through the CLOCK gates and nothing else.
    market = _market("2026-10-06T16:14:41Z", live_event=True)
    market["end_date_iso"] = None
    session = _RecordingSession()

    # Act
    row = evaluate(session, 0.0, market,
                   volume_24h=60_000.0, source="spread", now_iso=NOW)

    # Assert: refused, and refused for what the funnel found -- not on the
    # clock it was exempted from. Reaching the book stage at all is the proof
    # that the two clock gates were skipped rather than merely passed.
    assert row["eligible"] is False
    assert "in-play" not in row["reject_reason"]
    assert "pre-start" not in row["reject_reason"]
    assert any("trades" in u for u in session.urls)


def test_the_declaration_is_not_read_from_a_present_field():
    # Arrange: the stamp must be the flag itself, not "any value present" -- a
    # row carrying `_live_event: False` is a scanned market like any other.
    market = _market("2026-10-06T16:14:41Z")
    market["_live_event"] = False

    # Act
    row = evaluate(_ExplodingSession(), 0.0, market,
                   volume_24h=250_000.0, source="spread", now_iso=NOW)

    # Assert
    assert row["eligible"] is False
    assert "in-play" in row["reject_reason"]


# --- dashboard bucketing ----------------------------------------------------


def test_the_in_play_reason_buckets_as_one_gate():
    # Arrange: the reason embeds how long the event has run, so raw text would
    # make one dashboard card per market.
    reasons = ["in-play: event started 20m ago",
               "in-play: event started 3.0h ago"]

    # Act / Assert
    assert {_cause(r) for r in reasons} == {"in-play"}


def test_in_play_and_pre_start_stay_distinct_buckets():
    # Arrange / Act / Assert: a started match is not the same card as one that
    # has not begun.
    assert _cause("in-play: event started 20m ago") == "in-play"
    assert _cause("pre-start: event has not started (starts in 3.5h)") == "pre-start"


# --- the live volume bar (operator directive 2026-10-06) --------------------
# "set a lower bar for the volume on live ongoing games, since it's not a 24h
# market so naturally it will have less volume". A live event has not had the
# whole day to trade, so the permanent floor understates it -- but the live bar
# is LOWER, not absent: a live market with no tape at all is still refused.
#
# Measured on the live set 2026-10-06: 25 live main lines, $403 to $1.10M,
# median $82,095. $10,000 admits 13 and drops only the thin tail.


# A plain matchup that clears the identity gate: "A vs B" plus a known
# series keyword, which is the shape the CS2/Estral titles take.
_MATCHUP = {
    "title": "LoL: Estral Esports vs KaBuM! Ilha das Lendas",
    "slug": "lol-est-kbm-2026-10-06",
    "series_title": "League of Legends",
    "event_title": "LoL: Estral Esports vs KaBuM! Ilha das Lendas",
}


def test_a_live_market_between_the_two_bars_is_admitted():
    # Arrange: $48,486 -- under the $50,000 floor, over the live bar.
    ok, why = tradable(48_486.0, 0.5, **_MATCHUP,
                       min_volume_usd=50_000.0, live_event=True)

    # Assert
    assert ok is True, why


def test_a_live_market_under_the_live_bar_is_still_refused():
    # Arrange: one dollar under the live bar. The live bar is lower, not gone.
    ok, why = tradable(MIN_VOLUME_24H_LIVE - 1.0, 0.5, **_MATCHUP,
                       min_volume_usd=50_000.0, live_event=True)

    # Assert
    assert ok is False
    assert "24h volume" in why
    assert f"{MIN_VOLUME_24H_LIVE:,.0f}" in why


def test_the_volume_refusal_names_the_bar_that_actually_applied():
    # Arrange: the reason must quote the LIVE bar for a live market, otherwise
    # the funnel misreports why a market was dropped.
    ok, why = tradable(MIN_VOLUME_24H_LIVE - 1.0, 0.5, **_MATCHUP,
                       min_volume_usd=50_000.0, live_event=True)

    # Assert
    assert ok is False
    assert "50,000" not in why


def test_the_live_bar_is_lower_than_both_permanent_floors():
    # Arrange / Assert: the relationship is the point of the directive.
    assert MIN_VOLUME_24H_LIVE == 10_000.0
    assert MIN_VOLUME_24H_LIVE < 50_000.0 < 125_000.0


def test_live_volume_bar_is_only_chosen_for_a_declared_live_market():
    # Arrange / Act / Assert: keyed on the venue's declaration, never on the
    # clock -- a clock-in-play market is refused outright, so a bar it could
    # never reach would be dead code that looks shipped.
    assert live_volume_bar(True) == MIN_VOLUME_24H_LIVE
    assert live_volume_bar(False) is None
    assert live_volume_bar() is None


def test_a_scanned_market_below_the_floor_gets_no_live_bar():
    # Arrange: the same $48,486 that a live market is admitted at, on a market
    # the scan found. No declaration, so the permanent floor applies.
    ok, why = tradable(48_486.0, 0.5, **_MATCHUP, min_volume_usd=50_000.0)

    # Assert
    assert ok is False
    assert "24h volume" in why
    assert "50,000" in why


def test_the_live_exemption_does_not_bypass_a_later_gate():
    # Arrange: live and under the floor, but with no resolvable horizon -- the
    # exemption buys passage through ONE gate, not through the whole funnel.
    ok, why = tradable(None, 0.5, **_MATCHUP,
                       min_volume_usd=50_000.0, live_event=True)

    # Assert
    assert ok is False
    assert "volume unknown" in why

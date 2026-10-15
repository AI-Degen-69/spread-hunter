"""Rank-score: live competitive markets outrank flat long-dated ones (#392).

Liveness is the VENUE's declaration (`_live_event`), never a game clock: the
time-of-game purge (#597) removed every start/quarter/half/end reading, so a
row is live because `/events?live=true` said so and for no other reason.
"""
from __future__ import annotations

from scripts.filter_markets import rank_score, sort_eligible


def _live(**over):
    row = {
        "return_pct_day": 0.9,
        "movement_usd": 35000.0,
        "trade_count": 200,
        "range_cents": 8.0,
        "days_to_resolve": 0.2,
        "_live_event": True,
    }
    row.update(over)
    return row


def _flat(**over):
    row = {
        "return_pct_day": 0.9,
        "movement_usd": 2800.0,
        "trade_count": 24,
        "range_cents": 0.5,
        "days_to_resolve": 28.0,
    }
    row.update(over)
    return row


def test_live_outranks_flat_at_equal_return():
    assert rank_score(_live()) > rank_score(_flat())


def test_boost_comes_from_the_venue_live_flag_alone():
    # Identical rows differing only in the venue's live declaration: the boost,
    # not the flat-row penalty, decides this ordering.
    assert rank_score(_live()) > rank_score(_live(_live_event=False))


def test_missing_fields_rank_as_today():
    assert rank_score({"return_pct_day": 0.9}) == 0.9


def test_a_row_the_venue_does_not_declare_live_gets_no_boost():
    assert rank_score(_live(_live_event=False)) == 0.9


def test_a_row_with_no_live_declaration_at_all_gets_no_boost():
    row = _live()
    del row["_live_event"]
    assert rank_score(row) == 0.9


def test_flat_long_dated_scores_below_bare_return():
    assert rank_score(_flat()) < 0.9


def test_unmeasured_range_or_horizon_escapes_penalty():
    assert rank_score({"return_pct_day": 0.9, "range_cents": 0.5}) == 0.9
    assert rank_score({"return_pct_day": 0.9, "days_to_resolve": 28.0}) == 0.9


def test_live_row_ranks_first_on_universe_shaped_fixture():
    # Shaped like the 2026-10-06 rank: Senate-like rows carry window tape
    # but flat mids and far horizons; the venue-live row is in play now.
    senate_tx = {"return_pct_day": 1.0, "movement_usd": 19279.0,
                 "trade_count": 150, "range_cents": 1.0,
                 "days_to_resolve": 28.3}
    senate_ks = {"return_pct_day": 1.0, "movement_usd": 2783.0,
                 "trade_count": 24, "range_cents": 1.0,
                 "days_to_resolve": 28.3}
    live = {"return_pct_day": 0.9, "movement_usd": 35000.0,
            "trade_count": 200, "range_cents": 8.0,
            "days_to_resolve": 0.2, "_live_event": True}
    # The shipped ranking (sort_eligible): highest score wins.
    ranked = sort_eligible([senate_tx, senate_ks, live])
    assert ranked[0] is live

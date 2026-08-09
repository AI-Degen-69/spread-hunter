"""Hard market selector tests."""
from strategy.selector import book_allowed, identity_allowed, pair_books_allowed, top_depth_usd


def test_blocks_dynamic_esports_submarkets_and_live_names():
    for title in (
        "Dota 2 - Game 1 Winner",
        "Valorant Map 2 Winner",
        "CS2 Round 1 Winner",
        "Live: Team A vs Team B",
        "Set 1 Handicap",
    ):
        assert identity_allowed(title, "", "Sports", "Moneyline")[0] is False


def test_requires_primary_market_or_macro_category():
    assert identity_allowed("Team A vs Team B", "", "Sports", "Moneyline")[0]
    assert identity_allowed("Who wins?", "", "Politics", "")[0]
    assert identity_allowed("Who wins?", "", "", "Outright")[0]
    assert identity_allowed("Los Angeles Dodgers vs. Chicago Cubs", "", "", "", "", "", "MLB")[0]
    assert identity_allowed("Team A Map Winner", "", "Sports", "")[0] is False
    assert identity_allowed("Team A vs Team B (BO3)", "", "", "")[0] is False
    assert identity_allowed("Team A Game 10 Winner", "", "Sports", "Moneyline")[0]
    assert identity_allowed("Team A Game 1 Winner", "", "Sports", "Moneyline")[0] is False


def test_standalone_event_question_admitted_without_topic_keyword():
    # No "vs" shape means no Game 1/2 submarket variant is possible, so this
    # does not need a hardcoded macro keyword to be admitted.
    assert identity_allowed(
        "Strait of Hormuz traffic returns to normal by August 31?")[0]


def test_standalone_event_question_still_blocked_by_group_label():
    ok, reason = identity_allowed(
        "Will it happen?", "", "", "", "Player Props")
    assert not ok and reason == "carries a submarket group label"


def test_standalone_event_question_still_blocked_by_submarket_keyword():
    assert identity_allowed("Round 1 special outcome")[0] is False


def test_metadataless_matchup_is_admitted_only_when_primary_is_not_required():
    # The shape a pre-selector run/markets.json produces: title and slug, and
    # none of the five fields the league keyword is read from. Judged normally
    # it fails for want of evidence that was never collected.
    assert identity_allowed("Yankees vs Red Sox", "yankees-vs-red-sox")[0] is False
    assert identity_allowed("Yankees vs Red Sox", "yankees-vs-red-sox",
                            require_primary=False)[0] is True


def test_relaxed_identity_still_refuses_submarkets_and_group_labels():
    # `require_primary=False` drops the positive confirmation, never a
    # rejection. Blocked keywords are read off title and slug, which a
    # metadata-less spec still has.
    for title in ("Yankees vs Red Sox Game 1", "T1 vs GEN Map 2",
                  "Alcaraz vs Sinner - live", "Fury vs Usyk in-play"):
        assert identity_allowed(title, require_primary=False)[0] is False
    # A populated group label IS evidence, and survives on a metadata-less
    # spec precisely because it is present.
    ok, why = identity_allowed("Yankees vs Red Sox", "yankees-vs-red-sox",
                               market_group="Total Runs",
                               require_primary=False)
    assert not ok and why == "carries a submarket group label"


def test_top_depth_uses_best_three_bid_levels_as_notional():
    assert top_depth_usd({0.50: 4000, 0.49: 3000, 0.48: 2000, 0.47: 999}) == 0.5 * 4000 + 0.49 * 3000 + 0.48 * 2000


def test_each_token_must_pass_depth_and_spread_independently():
    good = {0.49: 4000, 0.48: 4000, 0.47: 4000}
    asks = {0.51: 4000}
    assert pair_books_allowed([("YES", good, asks), ("NO", good, asks)], 5000, 0.04)[0]
    shallow = {0.49: 1000, 0.48: 1000, 0.47: 1000}
    ok, reason = pair_books_allowed([("YES", shallow, asks), ("NO", good, asks)], 5000, 0.04)
    assert not ok and reason.startswith("YES:")


def test_rejects_wide_or_one_sided_books():
    good = {0.49: 12000}
    assert not book_allowed(good, {0.55: 12000}, 5000, 0.04)[0]
    assert not book_allowed(good, {}, 5000, 0.04)[0]

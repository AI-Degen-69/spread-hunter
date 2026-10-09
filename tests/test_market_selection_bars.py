"""tests/test_market_selection_bars.py - the permanent bars the market filter gates on.

`scripts/filter_markets.py` reads these two numbers once at import and prints
them in its `--help` output, so they are what an operator sees before deciding
to run a rank. They are also duplicated: `scoring/config.py` is what the filter
reads and `core_brain/config.py` carries the same fields for the Trader, and
nothing but a test stops the two drifting apart.

The trial knobs are pinned unset on purpose. A trial is a staged loosening an
operator opts into per run; a trial value left in the committed default would
make every rank a trial without saying so.
"""

from __future__ import annotations

from core_brain.config import MakerConfig
from scoring.config import MakerConfig as ScoringConfig

# The bars, lowered from $1,000 and $250,000 on 2026-08-25 by operator
# direction. A change here changes which markets the Trader is allowed to
# quote, so it is a decision, not a tuning detail.
MIN_TOP3_DEPTH_USD = 500.0
MIN_VOLUME_24H_USD = 125_000.0


def test_the_permanent_depth_bar_is_five_hundred_dollars():
    assert ScoringConfig().select_min_top3_depth_usd == MIN_TOP3_DEPTH_USD


def test_the_permanent_volume_bar_is_a_hundred_and_twenty_five_thousand():
    assert ScoringConfig().select_min_volume_24h_usd == MIN_VOLUME_24H_USD


def test_the_two_config_modules_carry_the_same_bars():
    """A filter gating on one bar while the Trader believes another is a
    universe the Trader did not choose."""
    scoring, maker = ScoringConfig(), MakerConfig()

    assert (scoring.select_min_top3_depth_usd
            == maker.select_min_top3_depth_usd == MIN_TOP3_DEPTH_USD)
    assert (scoring.select_min_volume_24h_usd
            == maker.select_min_volume_24h_usd == MIN_VOLUME_24H_USD)


def test_no_trial_bar_ships_set():
    """A committed trial value would make every rank a trial silently."""
    for cfg in (ScoringConfig(), MakerConfig()):
        assert cfg.select_min_top3_depth_usd_trial is None
        assert cfg.select_min_volume_24h_usd_trial is None


def test_the_help_screen_reports_the_bars_it_gates_on():
    """The operator reads `--help` before spending a few hundred requests."""
    import scripts.filter_markets as filter_markets

    assert filter_markets.MIN_TOP3_DEPTH_USD == MIN_TOP3_DEPTH_USD
    assert filter_markets.MIN_VOLUME_24H == MIN_VOLUME_24H_USD


# --- the boundaries themselves ------------------------------------------------
#
# The two gates are not written the same way, and a bar is only meaningful with
# its comparison attached. `tradable` refuses `volume < bar`, so a market with
# exactly the bar's volume is admitted. `book_allowed` refuses
# `depth <= bar`, so a book with exactly the bar's depth is not. These tests
# state which side of each bar is inside, so lowering a bar again cannot
# quietly change the answer.


def _book(depth_usd: float) -> tuple[dict, dict]:
    """One bid level worth `depth_usd`, and an ask a tick above it."""
    return {0.50: depth_usd / 0.50}, {0.51: 100.0}


def test_volume_exactly_at_the_bar_is_admitted():
    from scripts.filter_markets import tradable

    ok, reason = tradable(MIN_VOLUME_24H_USD, 5.0)

    assert ok, reason


def test_volume_one_dollar_under_the_bar_is_refused():
    from scripts.filter_markets import tradable

    ok, reason = tradable(MIN_VOLUME_24H_USD - 1.0, 5.0)

    assert not ok
    assert "24h volume" in reason


def test_depth_exactly_at_the_bar_is_refused():
    from scoring.selector import pair_books_allowed

    bids, asks = _book(MIN_TOP3_DEPTH_USD)
    ok, reason = pair_books_allowed(
        [("YES", bids, asks), ("NO", bids, asks)],
        min_depth_usd=MIN_TOP3_DEPTH_USD, max_spread=0.06)

    assert not ok
    assert "top-3 bid depth" in reason


def test_depth_just_above_the_bar_is_admitted():
    from scoring.selector import pair_books_allowed

    bids, asks = _book(MIN_TOP3_DEPTH_USD + 1.0)
    ok, reason = pair_books_allowed(
        [("YES", bids, asks), ("NO", bids, asks)],
        min_depth_usd=MIN_TOP3_DEPTH_USD, max_spread=0.06)

    assert ok, reason


def test_a_thin_leg_refuses_the_pair_even_when_the_other_leg_is_deep():
    """The depth bar is required on both tokens, not on their total."""
    from scoring.selector import pair_books_allowed

    deep_bids, deep_asks = _book(MIN_TOP3_DEPTH_USD * 10)
    thin_bids, thin_asks = _book(MIN_TOP3_DEPTH_USD)

    ok, reason = pair_books_allowed(
        [("YES", deep_bids, deep_asks), ("NO", thin_bids, thin_asks)],
        min_depth_usd=MIN_TOP3_DEPTH_USD, max_spread=0.06)

    assert not ok
    assert reason.startswith("NO:")


def test_the_permanent_spread_bar_is_two_point_zero_five_cents():
    assert ScoringConfig().select_max_book_spread == 0.0205
    assert MakerConfig().select_max_book_spread == 0.0205


def test_the_filter_markets_spread_bar_matches_config():
    import scripts.filter_markets as filter_markets
    assert filter_markets.MAX_BOOK_SPREAD == 0.0205


def test_book_allowed_default_spread_gate_boundary():
    import pytest
    from scoring.selector import book_allowed

    # 0.49 / 0.5105 has exact spread 0.0205; must be admitted with sufficient depth
    ok, reason, _, spread = book_allowed({0.49: 20_000.0}, {0.5105: 20_000.0})
    assert ok, reason
    assert spread == pytest.approx(0.0205)

    # 0.4794 / 0.5000 has spread 0.0206 > 0.0205; must be refused
    ok, reason, _, _ = book_allowed({0.4794: 20_000.0}, {0.5000: 20_000.0})
    assert not ok
    assert "spread 0.0206 > 0.0205" in reason


def test_pair_books_allowed_refuses_wide_book_by_default():
    from scoring.selector import pair_books_allowed

    # YES book 2c spread, NO book 3c spread
    yes_bids, yes_asks = {0.49: 20_000.0}, {0.51: 20_000.0}
    no_bids, no_asks = {0.485: 20_000.0}, {0.515: 20_000.0}

    ok, reason = pair_books_allowed([("YES", yes_bids, yes_asks), ("NO", no_bids, no_asks)])
    assert not ok
    assert "NO: spread 0.0300 > 0.0205" in reason


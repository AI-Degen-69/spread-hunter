"""The quote window is the engine's, never the feed row's.

A feed row used to carry `max_spread` (the venue's rewardsMaxSpread) and
`trader_loop.spec_to_cfg` fed it into the per-market config as
`max_spread_from_mid` -- so a Fed market quoted with a 2.5c window and a
sports market 4.5c, while the engine said 15c. The rewards purge (#594) removed
that override; these tests pin the removal so the venue's reward field can
never throttle the window again.
"""
from __future__ import annotations

from dataclasses import replace

from core_brain.config import MakerConfig
from core_brain.trader_loop import _market_cfg as spec_to_cfg


def _row(**extra):
    row = {
        "min_size": 5,
        "shares": 10,
        "tick": 0.01,
        "title": "Test Market",
        "range_cents": 5.0,
        "velocity_measured_at": 1700000000.0,
    }
    row.update(extra)
    return row


def test_feed_row_reward_field_does_not_cap_the_window():
    base = MakerConfig(max_spread_from_mid=0.15)
    # The venue's rewardsMaxSpread as the ranker wrote it for years.
    cfg = spec_to_cfg(base, _row(max_spread=2.5))
    assert cfg.max_spread_from_mid == 0.15


def test_no_daily_income_field_on_the_market_config():
    base = MakerConfig()
    cfg = spec_to_cfg(base, _row(daily=1234.5))
    assert not hasattr(cfg, "market_daily_rate")
    assert cfg.max_spread_from_mid == base.max_spread_from_mid


def test_engine_window_override_survives_through_the_ranker_row():
    base = MakerConfig(max_spread_from_mid=0.20)
    cfg = spec_to_cfg(base, _row(max_spread=6.5, daily=99.0))
    assert cfg.max_spread_from_mid == 0.20


def test_objective_is_spread_capture_only():
    cfg = spec_to_cfg(MakerConfig(), _row())
    assert cfg.objective == "spread_capture"


def test_rewards_mode_fields_are_gone():
    base = MakerConfig()
    for field in ("reward_min_payout_usd", "reward_floor_multiple",
                  "est_reward_pool_usd", "rebate_rate", "market_daily_rate",
                  "marginal_return_floor", "allocation_budget",
                  "max_market_frac", "spread_capture_frac",
                  "spread_capture_default_spread"):
        assert not hasattr(base, field), f"{field} must be deleted (#594)"


def test_venue_fee_fields_stay():
    """fee_rate is real venue economics (pair_scanner charges it on
    completions); maker_fee is the venue's takerOnly fact. Neither is a
    rebate and both survive the purge."""
    base = MakerConfig()
    assert base.fee_rate == 0.07
    assert base.maker_fee == 0.0

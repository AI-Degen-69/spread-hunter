"""Tests for core_brain/risk.py (#632: parameter standardization & risk gates)."""
from __future__ import annotations

import pytest

from core_brain.config import MakerConfig
from core_brain.quotes import Inventory
from core_brain.risk import (
    BookHealth,
    book_health,
    skew_offset,
)


def test_skew_offset_zero_when_flat():
    cfg = MakerConfig(bot_pos_skew_max_offset_cents=0.015, bot_risk_exposure_max_naked_usd=10.0)
    inv = Inventory(up_shares=10, down_shares=10, up_cost=5.0, down_cost=5.0)
    assert skew_offset(cfg, inv, "UP") == 0.0
    assert skew_offset(cfg, inv, "DOWN") == 0.0


def test_skew_offset_scales_with_max_skew_offset():
    # Long UP: 10 shares @ 0.50 -> $5.00 cost against max_naked_usd=10.0 => 50% utilization
    cfg = MakerConfig(bot_pos_skew_max_offset_cents=0.020, bot_risk_exposure_max_naked_usd=10.0)
    inv = Inventory(up_shares=10, down_shares=0, up_cost=5.0, down_cost=0.0)

    # Naked side (UP) gets positive push (quote further from mid)
    up_push = skew_offset(cfg, inv, "UP")
    assert up_push == pytest.approx(0.010)

    # Flattening side (DOWN) gets negative push (quote closer to mid)
    dn_push = skew_offset(cfg, inv, "DOWN")
    assert dn_push == pytest.approx(-0.010)


def test_book_health_checks_min_book_depth_shares():
    cfg = MakerConfig(bot_quote_book_min_depth_shares=200.0, bot_quote_book_max_spread_usd=0.06)

    # Thin book (150 shares < 200)
    thin_book = {
        "best_bid": 0.48,
        "best_ask": 0.52,
        "bids": {0.48: 150.0},
        "asks": {0.52: 500.0},
    }
    bh_thin = book_health(thin_book, cfg)
    assert not bh_thin.ok
    assert "book too thin 150sh < 200sh" in bh_thin.reason

    # Healthy book (250 shares >= 200)
    healthy_book = {
        "best_bid": 0.48,
        "best_ask": 0.52,
        "bids": {0.48: 250.0},
        "asks": {0.52: 500.0},
    }
    bh_ok = book_health(healthy_book, cfg)
    assert bh_ok.ok
    assert bh_ok.reason == ""

from core_brain.risk import size_for

_FLAT = Inventory(up_shares=0, down_shares=0, up_cost=0.0, down_cost=0.0)


def _kelly_cfg(**kw):
    base = dict(bot_quote_size_mode="kelly", bot_quote_bank_baseline_usd=1000.0,
                bot_kelly_n_closes=44, bot_kelly_pct=0.4,
                bot_quote_kelly_pct=0.5, bot_quote_kelly_min_closes=30)
    base.update(kw)
    return MakerConfig(**base)


def test_kelly_positive_sizes_from_kelly_not_couple_pct():
    # dollars mode would give max(1000*0.01, 10)=10 -> int(10/0.92)=10 shares.
    cfg = _kelly_cfg()
    assert size_for(cfg, _FLAT, "UP", 0.46, pair_price=0.92) == int(200.0 / 0.92)


def test_kelly_non_positive_quotes_nothing():
    assert size_for(_kelly_cfg(bot_kelly_pct=0.0), _FLAT, "UP", 0.46, pair_price=0.92) == 0
    assert size_for(_kelly_cfg(bot_kelly_pct=-0.25), _FLAT, "UP", 0.46, pair_price=0.92) == 0


def test_kelly_below_min_closes_falls_back_to_dollars_byte_for_byte():
    for price in (0.35, 0.46, 0.5, 0.7):
        kelly = _kelly_cfg(bot_kelly_n_closes=10)
        dollars = MakerConfig(bot_quote_size_mode="dollars", bot_quote_bank_baseline_usd=1000.0)
        assert size_for(kelly, _FLAT, "UP", price, pair_price=0.92) == \
            size_for(dollars, _FLAT, "UP", price, pair_price=0.92)


def test_kelly_no_edge_still_sizes_the_balancing_buy():
    # No edge stops new exposure; leaving the light side of an unmatched leg
    # at zero would strand a single filled buy -- the failure the pair rule
    # exists to prevent. The repair sizes by the dollars rule, capped at the
    # deficit.
    inv = Inventory(up_shares=100, down_shares=0, up_cost=46.0, down_cost=0.0)
    cfg = _kelly_cfg(bot_kelly_pct=0.0, bot_pos_gate_strict_pairs_on=True)
    dollars = MakerConfig(bot_quote_size_mode="dollars", bot_quote_bank_baseline_usd=1000.0)
    light = size_for(cfg, inv, "DOWN", 0.46, pair_price=0.92)
    assert light == size_for(dollars, inv, "DOWN", 0.46, pair_price=0.92) > 0
    assert size_for(cfg, inv, "UP", 0.46, pair_price=0.92) == 0


def test_kelly_non_finite_falls_back_to_dollars_instead_of_raising():
    # A NaN f* passes "f_star <= 0.0" and "min(f_star, 1.0)", then int(nan)
    # raises. Unusable input must degrade to dollars, never crash a rotation.
    kelly = _kelly_cfg(bot_kelly_pct=float("nan"))
    dollars = MakerConfig(bot_quote_size_mode="dollars", bot_quote_bank_baseline_usd=1000.0)
    assert size_for(kelly, _FLAT, "UP", 0.46, pair_price=0.92) == \
        size_for(dollars, _FLAT, "UP", 0.46, pair_price=0.92)


def test_kelly_missing_stats_fall_back_to_dollars_byte_for_byte():
    kelly = _kelly_cfg(bot_kelly_n_closes=0, bot_kelly_pct=None)
    dollars = MakerConfig(bot_quote_size_mode="dollars", bot_quote_bank_baseline_usd=1000.0)
    for price in (0.35, 0.46, 0.7):
        assert size_for(kelly, _FLAT, "DOWN", price, pair_price=0.92) == \
            size_for(dollars, _FLAT, "DOWN", price, pair_price=0.92)


def test_kelly_implied_fraction_capped_never_levers():
    # f* = 3.0 caps to 1.0 before the 0.5 multiplier: alloc = 1000 * 1.0 * 0.5 = 500
    cfg = _kelly_cfg(bot_kelly_pct=3.0)
    assert size_for(cfg, _FLAT, "UP", 0.46, pair_price=0.92) == int(500.0 / 0.92)


def test_kelly_tiny_edge_cannot_clear_venue_floor():
    cfg = _kelly_cfg(bot_quote_bank_baseline_usd=100.0, bot_kelly_pct=0.05)
    assert size_for(cfg, _FLAT, "UP", 0.46, pair_price=0.92) == 0


def test_kelly_max_shares_clamp_still_applies():
    cfg = _kelly_cfg(bot_quote_size_max_shares=100.0)
    assert size_for(cfg, _FLAT, "UP", 0.46, pair_price=0.92) == 100


def test_kelly_rides_inside_the_naked_taper():
    # 50 naked UP at 0.50, naked budget 100 -> u=0.5 -> taper x0.25.
    # base = int(200/0.92) = 217; remainder cap 50/0.46 = 108 shares, no bind.
    inv = Inventory(up_shares=100, down_shares=0, up_cost=50.0, down_cost=0.0)
    cfg = _kelly_cfg(bot_risk_exposure_max_naked_usd=100.0, bot_pos_gate_strict_pairs_on=False)
    base = int(200.0 / 0.92)
    assert size_for(cfg, inv, "UP", 0.46, pair_price=0.92) == int(base * 0.25)


def test_kelly_light_side_not_tapered():
    inv = Inventory(up_shares=100, down_shares=0, up_cost=50.0, down_cost=0.0)
    cfg = _kelly_cfg(bot_risk_exposure_max_naked_usd=100.0, bot_pos_gate_strict_pairs_on=False)
    assert size_for(cfg, inv, "DOWN", 0.46, pair_price=0.92) == int(200.0 / 0.92)


def test_kelly_mode_unset_means_zero_change():
    # Kelly fields set but mode is dollars -> identical to dollars without them.
    plain = MakerConfig(bot_quote_size_mode="dollars", bot_quote_bank_baseline_usd=1000.0)
    loaded = _kelly_cfg(bot_quote_size_mode="dollars")
    for price in (0.35, 0.46, 0.7):
        assert size_for(loaded, _FLAT, "UP", price, pair_price=0.92) == \
            size_for(plain, _FLAT, "UP", price, pair_price=0.92)

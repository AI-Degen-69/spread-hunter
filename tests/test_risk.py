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
    cfg = MakerConfig(max_skew_offset=0.015, max_naked_usd=10.0)
    inv = Inventory(up_shares=10, down_shares=10, up_cost=5.0, down_cost=5.0)
    assert skew_offset(cfg, inv, "UP") == 0.0
    assert skew_offset(cfg, inv, "DOWN") == 0.0


def test_skew_offset_scales_with_max_skew_offset():
    # Long UP: 10 shares @ 0.50 -> $5.00 cost against max_naked_usd=10.0 => 50% utilization
    cfg = MakerConfig(max_skew_offset=0.020, max_naked_usd=10.0)
    inv = Inventory(up_shares=10, down_shares=0, up_cost=5.0, down_cost=0.0)

    # Naked side (UP) gets positive push (quote further from mid)
    up_push = skew_offset(cfg, inv, "UP")
    assert up_push == pytest.approx(0.010)

    # Flattening side (DOWN) gets negative push (quote closer to mid)
    dn_push = skew_offset(cfg, inv, "DOWN")
    assert dn_push == pytest.approx(-0.010)


def test_book_health_checks_min_book_depth_shares():
    cfg = MakerConfig(min_book_depth_shares=200.0, max_book_spread=0.06)

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

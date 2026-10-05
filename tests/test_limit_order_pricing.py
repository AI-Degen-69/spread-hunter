"""Tests for rolling range extraction and dynamic limit order price calculation with minimum safety floor."""
from __future__ import annotations

import pytest

from core_brain.quotes import (
    calculate_limit_order_price,
    rolling_range,
)


def test_calculate_limit_order_price_empty_history_uses_min_floor():
    # current_price = 0.50, empty history -> final_offset = 0.02 -> price = 0.48
    price = calculate_limit_order_price(0.50, [], range_fraction=0.4, min_floor=0.02)
    assert price == 0.48


def test_calculate_limit_order_price_calm_market_clamps_to_safety_floor():
    # Price history has 1 cent range (0.50 to 0.51).
    # dynamic_offset = 0.01 * 0.4 = 0.004 < min_floor (0.02)
    # final_offset must be 0.02
    history = [0.50, 0.505, 0.51, 0.50]
    price = calculate_limit_order_price(0.50, history, range_fraction=0.4, min_floor=0.02)
    assert price == 0.48


def test_calculate_limit_order_price_volatile_market_scales_with_range():
    # Price history has 10 cent range (0.50 to 0.60).
    # dynamic_offset = 0.10 * 0.4 = 0.04 > min_floor (0.02)
    # final_offset = 0.04 -> price = 0.50 - 0.04 = 0.46
    history = [0.50, 0.55, 0.60, 0.52]
    price = calculate_limit_order_price(0.50, history, range_fraction=0.4, min_floor=0.02)
    assert price == 0.46


def test_calculate_limit_order_price_enforces_price_floor_at_one_cent():
    # Extreme case: price is 0.015, offset is 0.02 -> price clamped to at least 0.01
    price = calculate_limit_order_price(0.015, [], range_fraction=0.4, min_floor=0.02)
    assert price == 0.01


def test_rolling_range_calculates_high_minus_low():
    assert rolling_range([]) == 0.0
    assert rolling_range([0.48, 0.52, 0.50]) == 0.04

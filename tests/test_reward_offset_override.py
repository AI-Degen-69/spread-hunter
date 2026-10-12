"""`HUNTER_REWARD_OFFSET` was PURGED 2026-10-12 (operator directive).

It was a synthetic sub-cent override (0.005) that forced touch-reach quotes at
0.495 -- artificial placement on a book that runs a standard 1-cent grid. It
produced off-grid resting orders the venue rejects and false test signal. The
offset is now driven solely by the dynamic range system; there is no per-run
reward-offset knob.

These tests pin the purge: the env var is ignored, the offset follows the
dynamic system, and every resting quote floors to a clean whole cent.
"""
from __future__ import annotations

import os
from unittest import mock

import pytest

from core_brain.config import MakerConfig, load
from core_brain.quotes import Inventory, quote_resting_price


class TestRewardOffsetPurged:
    def test_the_env_var_is_ignored(self):
        # Setting the retired override must not move the offset any more.
        with mock.patch.dict(os.environ, {"HUNTER_REWARD_OFFSET": "0.005"}):
            cfg = load()
        assert cfg.reward_offset == 0.020  # shipped default, override ignored

    def test_a_garbage_value_is_ignored_not_raised(self):
        # The parse block is gone, so a bad value is simply not read.
        for bad in ("nan", "inf", "abc", "1.5"):
            with mock.patch.dict(os.environ, {"HUNTER_REWARD_OFFSET": bad}):
                assert load().reward_offset == 0.020

    def test_shipped_default_is_unchanged(self):
        assert MakerConfig().reward_offset == 0.020


class TestQuotesFloorToWholeCent:
    """Every resting quote lands on the standard 1-cent grid, floored down."""

    def test_a_sub_cent_target_floors_to_the_cent(self):
        # mid 0.545, dynamic offset + band -> raw 0.4xxx; must floor to a cent.
        cfg = MakerConfig(
            dynamic_offset_enabled=True, dynamic_offset_multiplier=0.5,
            dynamic_offset_min_cents=1, dynamic_offset_max_cents=20,
            range_cents=15.66, velocity_measured_at=None, price_tick=0.01,
            max_spread_from_mid=0.15,
        )
        book = {"best_bid": 0.54, "best_ask": 0.55}
        price, provisional, *_ = quote_resting_price(cfg, Inventory(), "UP", book)
        # Whole-cent, and never above the provisional target.
        assert abs(price * 100 - round(price * 100)) < 1e-9
        assert price <= provisional + 1e-9

    def test_no_touch_reach_at_a_sub_cent_bid(self):
        # The old override forced a quote to 0.495 on a 0.495/0.505 book. That
        # is gone: even with band risk zeroed, the quote floors to the cent and
        # cannot rest at a half-cent touch.
        from dataclasses import replace
        book = {"best_bid": 0.495, "best_ask": 0.505}
        cfg = replace(MakerConfig(price_tick=0.01, price_risk_widen=0.0),
                      reward_offset=0.005, dynamic_offset_enabled=False)
        price, *_ = quote_resting_price(cfg, Inventory(), "UP", book)
        # Floored to a whole cent at or below the touch -- never 0.495.
        assert abs(price * 100 - round(price * 100)) < 1e-9
        assert price <= 0.49 + 1e-9

    def test_zero_offset_still_floors_to_the_grid(self):
        cfg = MakerConfig(
            dynamic_offset_enabled=False, reward_offset=0.0,
            min_reward_offset=0.0, price_risk_widen=0.0, price_tick=0.01,
        )
        book = {"best_bid": 0.495, "best_ask": 0.505}
        price, *_ = quote_resting_price(cfg, Inventory(), "UP", book)
        assert abs(price * 100 - round(price * 100)) < 1e-9

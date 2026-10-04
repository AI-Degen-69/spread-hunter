"""Tests for opt-in dynamic integer-cent quote offsets and tournament presets (#370)."""
from __future__ import annotations

import os
from unittest import mock

import pytest

from core_brain.config import (
    MakerConfig,
    TOURNAMENT_PRESETS,
    apply_tournament_preset,
    load,
)
from core_brain.quotes import (
    Inventory,
    dynamic_offset_for,
    quote_resting_price,
    _decide_quotes_from_mid,
)


NOW = 1_788_000_000.0


def test_dynamic_offset_disabled_by_default():
    cfg = MakerConfig()
    offset, tag = dynamic_offset_for(cfg, now=NOW)
    assert offset == cfg.reward_offset
    assert tag == "static"


def test_dynamic_offset_handles_missing_range_telemetry():
    cfg = MakerConfig(dynamic_offset_enabled=True, range_cents=None)
    offset, tag = dynamic_offset_for(cfg, now=NOW)
    assert offset == cfg.reward_offset
    assert tag == "static_missing_range"


def test_dynamic_offset_handles_stale_telemetry():
    # 1000s old > 900s max age
    cfg = MakerConfig(
        dynamic_offset_enabled=True,
        range_cents=4.0,
        velocity_measured_at=NOW - 1000.0,
        dynamic_offset_max_age_sec=900.0,
    )
    offset, tag = dynamic_offset_for(cfg, now=NOW)
    assert offset == cfg.reward_offset
    assert tag == "static_stale_range"


def test_dynamic_offset_calculates_integer_cents_and_rounds_cleanly():
    # 3.2c * 0.5 = 1.6c -> rounds to 2c -> 0.02
    cfg = MakerConfig(
        dynamic_offset_enabled=True,
        dynamic_offset_multiplier=0.5,
        dynamic_offset_min_cents=1,
        dynamic_offset_max_cents=4,
        range_cents=3.2,
        velocity_measured_at=NOW - 60.0,
    )
    offset, tag = dynamic_offset_for(cfg, now=NOW)
    assert offset == 0.02
    assert tag == "dynamic_2c"
    # Verify integer cents: no fractional half-cents (e.g. 0.025 or 0.015)
    assert round(offset * 100.0) == offset * 100.0


def test_dynamic_offset_clamps_to_min_and_max():
    # 0.5c * 0.5 = 0.25c -> clamp to min 1c -> 0.01
    cfg_low = MakerConfig(
        dynamic_offset_enabled=True,
        dynamic_offset_multiplier=0.5,
        dynamic_offset_min_cents=1,
        dynamic_offset_max_cents=4,
        range_cents=0.5,
        velocity_measured_at=NOW - 30.0,
    )
    offset_low, tag_low = dynamic_offset_for(cfg_low, now=NOW)
    assert offset_low == 0.01
    assert tag_low == "dynamic_1c"

    # 15.0c * 0.5 = 7.5c -> clamp to max 4c -> 0.04
    cfg_high = MakerConfig(
        dynamic_offset_enabled=True,
        dynamic_offset_multiplier=0.5,
        dynamic_offset_min_cents=1,
        dynamic_offset_max_cents=4,
        range_cents=15.0,
        velocity_measured_at=NOW - 30.0,
    )
    offset_high, tag_high = dynamic_offset_for(cfg_high, now=NOW)
    assert offset_high == 0.04
    assert tag_high == "dynamic_4c"


def test_quote_resting_price_integrates_dynamic_offset():
    book = {"best_bid": 0.495, "best_ask": 0.505}  # mid 0.500
    # Dynamic offset produces 3c base
    cfg = MakerConfig(
        dynamic_offset_enabled=True,
        dynamic_offset_multiplier=0.5,
        dynamic_offset_min_cents=1,
        dynamic_offset_max_cents=5,
        range_cents=6.0,
        velocity_measured_at=NOW - 60.0,
        price_risk_widen=0.0,  # isolate base offset
        coinflip_halfwidth=0.0,
    )
    price, provisional, _, _ = quote_resting_price(cfg, Inventory(), "UP", book, now=NOW)
    # mid 0.500 - 0.03 base = 0.470
    assert provisional == pytest.approx(0.470)
    assert price == pytest.approx(0.470)


def test_decide_quotes_refuses_when_dynamic_pair_sum_exceeds_dollar():
    up_book = {"best_bid": 0.50, "best_ask": 0.51, "token_id": "tok_up"}
    down_book = {"best_bid": 0.50, "best_ask": 0.51, "token_id": "tok_dn"}
    # Force an edge case where dynamic base or price calculation would hit >= 1.00
    # By mocking quote_resting_price to return 0.51 each, pair sum = 1.02 >= 1.00
    cfg = MakerConfig(dynamic_offset_enabled=True, range_cents=2.0)
    with mock.patch("core_brain.quotes.quote_resting_price", return_value=(0.51, 0.51, None, 1.0)):
        intents, reason = _decide_quotes_from_mid(cfg, up_book, down_book, Inventory(), t_remaining=1000.0)
        assert intents == []
        assert "dynamic_pair_sum" in reason
        assert ">= 1.00" in reason


def test_tournament_presets_registry_contents():
    expected_presets = {"control", "conservative", "balanced", "aggressive"}
    assert set(TOURNAMENT_PRESETS.keys()) == expected_presets

    control = TOURNAMENT_PRESETS["control"]
    assert not control["dynamic_offset_enabled"]
    assert control["reward_offset"] == 0.020

    aggressive = TOURNAMENT_PRESETS["aggressive"]
    assert aggressive["dynamic_offset_enabled"]
    assert aggressive["dynamic_offset_multiplier"] == 0.25
    assert aggressive["dynamic_offset_min_cents"] == 1
    assert aggressive["dynamic_offset_max_cents"] == 2


def test_apply_tournament_preset():
    base = MakerConfig()
    agg = apply_tournament_preset(base, "aggressive")
    assert agg.dynamic_offset_enabled is True
    assert agg.dynamic_offset_multiplier == 0.25
    assert agg.dynamic_offset_min_cents == 1
    assert agg.dynamic_offset_max_cents == 2

    cons = apply_tournament_preset(base, "conservative")
    assert cons.dynamic_offset_enabled is True
    assert cons.dynamic_offset_multiplier == 0.75
    assert cons.dynamic_offset_min_cents == 2
    assert cons.dynamic_offset_max_cents == 5

    ctrl = apply_tournament_preset(agg, "control")
    assert ctrl.dynamic_offset_enabled is False

    with pytest.raises(ValueError, match="Unknown tournament preset"):
        apply_tournament_preset(base, "yolo")


def test_load_config_with_preset_and_env_overrides():
    with mock.patch.dict(os.environ, {"HUNTER_TOURNAMENT_PRESET": "balanced"}):
        cfg = load()
        assert cfg.dynamic_offset_enabled is True
        assert cfg.dynamic_offset_multiplier == 0.50
        assert cfg.dynamic_offset_min_cents == 1
        assert cfg.dynamic_offset_max_cents == 4

    # Env override on top of preset
    with mock.patch.dict(os.environ, {
        "HUNTER_TOURNAMENT_PRESET": "aggressive",
        "HUNTER_DYNAMIC_OFFSET_MAX_CENTS": "3",
    }):
        cfg_custom = load()
        assert cfg_custom.dynamic_offset_enabled is True
        assert cfg_custom.dynamic_offset_multiplier == 0.25
        assert cfg_custom.dynamic_offset_min_cents == 1
        assert cfg_custom.dynamic_offset_max_cents == 3

    # Unknown preset raises ValueError
    with mock.patch.dict(os.environ, {"HUNTER_TOURNAMENT_PRESET": "unknown_arm"}):
        with pytest.raises(ValueError, match="HUNTER_TOURNAMENT_PRESET"):
            load()


def test_market_cfg_wires_range_telemetry():
    from core_brain.trader_loop import _market_cfg

    base = MakerConfig()
    spec = {
        "cid": "0xabc",
        "title": "Test Market",
        "range_cents": 5.5,
        "velocity_measured_at": 1_788_000_123.0,
    }
    cfg = _market_cfg(base, spec)
    assert cfg.range_cents == 5.5
    assert cfg.velocity_measured_at == 1_788_000_123.0


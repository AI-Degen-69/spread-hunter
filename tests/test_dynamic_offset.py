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


def test_dynamic_offset_enabled_by_default():
    """The loose system baseline has dynamic offset ON by default (2026-10-12).

    With no range telemetry yet it still falls back to the static reward_offset
    and tags 'static_missing_range' -- the mechanism is unchanged, only the
    default enable flag flipped to True.
    """
    cfg = MakerConfig()
    offset, tag = dynamic_offset_for(cfg, now=NOW)
    assert offset == cfg.reward_offset
    assert tag == "static_missing_range"


def test_dynamic_offset_handles_missing_range_telemetry():
    cfg = MakerConfig(bot_quote_gate_dynamic_offset_on=True, bot_quote_telemetry_range_cents=None)
    offset, tag = dynamic_offset_for(cfg, now=NOW)
    assert offset == cfg.bot_quote_spread_static_offset_cents
    assert tag == "static_missing_range"


def test_dynamic_offset_handles_stale_telemetry():
    # 1000s old > 900s max age
    cfg = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True,
        bot_quote_telemetry_range_cents=4.0,
        bot_quote_telemetry_velocity_measured_at=NOW - 1000.0,
        bot_quote_spread_offset_max_age_sec=900.0,
    )
    offset, tag = dynamic_offset_for(cfg, now=NOW)
    assert offset == cfg.bot_quote_spread_static_offset_cents
    assert tag == "static_stale_range"


def test_dynamic_offset_calculates_integer_cents_and_rounds_cleanly():
    # 3.2c * 0.5 = 1.6c -> rounds to 2c -> 0.02
    cfg = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True,
        bot_quote_spread_offset_multiplier=0.5,
        bot_quote_spread_offset_min_cents=1,
        bot_quote_spread_offset_max_cents=4,
        bot_quote_telemetry_range_cents=3.2,
        bot_quote_telemetry_velocity_measured_at=NOW - 60.0,
    )
    offset, tag = dynamic_offset_for(cfg, now=NOW)
    assert offset == 0.02
    assert tag == "dynamic_2c"
    # Verify integer cents: no fractional half-cents (e.g. 0.025 or 0.015)
    assert round(offset * 100.0) == offset * 100.0


def test_dynamic_offset_clamps_to_min_and_max():
    # 0.5c * 0.5 = 0.25c -> clamp to min 1c -> 0.01
    cfg_low = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True,
        bot_quote_spread_offset_multiplier=0.5,
        bot_quote_spread_offset_min_cents=1,
        bot_quote_spread_offset_max_cents=4,
        bot_quote_telemetry_range_cents=0.5,
        bot_quote_telemetry_velocity_measured_at=NOW - 30.0,
    )
    offset_low, tag_low = dynamic_offset_for(cfg_low, now=NOW)
    assert offset_low == 0.01
    assert tag_low == "dynamic_1c"

    # 15.0c * 0.5 = 7.5c -> clamp to max 4c -> 0.04
    cfg_high = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True,
        bot_quote_spread_offset_multiplier=0.5,
        bot_quote_spread_offset_min_cents=1,
        bot_quote_spread_offset_max_cents=4,
        bot_quote_telemetry_range_cents=15.0,
        bot_quote_telemetry_velocity_measured_at=NOW - 30.0,
    )
    offset_high, tag_high = dynamic_offset_for(cfg_high, now=NOW)
    assert offset_high == 0.04
    assert tag_high == "dynamic_4c"


def test_quote_resting_price_integrates_dynamic_offset():
    book = {"best_bid": 0.495, "best_ask": 0.505}  # mid 0.500
    # Dynamic offset produces 3c base
    cfg = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True,
        bot_quote_spread_offset_multiplier=0.5,
        bot_quote_spread_offset_min_cents=1,
        bot_quote_spread_offset_max_cents=5,
        bot_quote_telemetry_range_cents=6.0,
        bot_quote_telemetry_velocity_measured_at=NOW - 60.0,
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
    cfg = MakerConfig(bot_quote_gate_dynamic_offset_on=True, bot_quote_telemetry_range_cents=2.0)
    with mock.patch("core_brain.quotes.quote_resting_price", return_value=(0.51, 0.51, None, 1.0)):
        intents, reason = _decide_quotes_from_mid(cfg, up_book, down_book, Inventory(), t_remaining=1000.0)
        assert intents == []
        assert "dynamic_pair_sum" in reason
        assert ">= 1.00" in reason


def test_tournament_presets_registry_contents():
    expected_presets = {"conservative", "balanced", "aggressive", "prudent", "hybrid", "baseline"}
    assert set(TOURNAMENT_PRESETS.keys()) == expected_presets

    # The legacy capped "control" arm was purged (broken 4.5c cap + widen
    # snap-back). It must not be reintroduced.
    assert "control" not in TOURNAMENT_PRESETS

    aggressive = TOURNAMENT_PRESETS["aggressive"]
    assert aggressive["dynamic_offset_enabled"]
    assert aggressive["dynamic_offset_multiplier"] == 0.25
    assert aggressive["dynamic_offset_min_cents"] == 2
    assert aggressive["dynamic_offset_max_cents"] == 3

    balanced = TOURNAMENT_PRESETS["balanced"]
    assert balanced["dynamic_offset_enabled"]
    assert balanced["dynamic_offset_multiplier"] == 0.50
    assert balanced["dynamic_offset_min_cents"] == 2
    assert balanced["dynamic_offset_max_cents"] == 4
    assert balanced["requote_hold_queue_shares"] == 500.0
    assert balanced["requote_hold_below_target"] == 0.08

    prudent = TOURNAMENT_PRESETS["prudent"]
    assert prudent["dynamic_offset_enabled"]
    assert prudent["dynamic_offset_multiplier"] == 0.60
    assert prudent["dynamic_offset_min_cents"] == 2
    assert prudent["dynamic_offset_max_cents"] == 4
    assert prudent["requote_hold_queue_shares"] == 500.0
    assert prudent["requote_hold_below_target"] == 0.08


def test_apply_tournament_preset():
    base = MakerConfig()
    agg = apply_tournament_preset(base, "aggressive")
    assert agg.dynamic_offset_enabled is True
    assert agg.dynamic_offset_multiplier == 0.25
    assert agg.dynamic_offset_min_cents == 2
    assert agg.dynamic_offset_max_cents == 3
    assert agg.requote_hold_queue_shares == 500.0
    assert agg.requote_hold_below_target == 0.08

    bal = apply_tournament_preset(base, "balanced")
    assert bal.dynamic_offset_enabled is True
    assert bal.dynamic_offset_multiplier == 0.50
    assert bal.dynamic_offset_min_cents == 2
    assert bal.dynamic_offset_max_cents == 4
    assert bal.requote_hold_queue_shares == 500.0
    assert bal.requote_hold_below_target == 0.08

    cons = apply_tournament_preset(base, "conservative")
    assert cons.dynamic_offset_enabled is True
    assert cons.dynamic_offset_multiplier == 0.75
    assert cons.dynamic_offset_min_cents == 2
    assert cons.dynamic_offset_max_cents == 5
    assert cons.requote_hold_queue_shares == 500.0
    assert cons.requote_hold_below_target == 0.08

    pru = apply_tournament_preset(base, "prudent")
    assert pru.dynamic_offset_enabled is True
    assert pru.dynamic_offset_multiplier == 0.60
    assert pru.dynamic_offset_min_cents == 2
    assert pru.dynamic_offset_max_cents == 4
    assert pru.requote_hold_queue_shares == 500.0
    assert pru.requote_hold_below_target == 0.08

    with pytest.raises(ValueError, match="Unknown tournament preset"):
        apply_tournament_preset(base, "yolo")


def test_tournament_presets_no_longer_carry_a_stop():
    """#619: the entry-relative stop is a run setting, not a per-arm one.

    The arms used to carry distinct cent stops ordered by fill patience. That
    rationale is gone with the static stop it was calibrated against: a fixed
    cent drop is not a per-arm edge, and the entry-relative trigger plus the
    shared emergency floor apply to every profile identically.
    """
    tournament_arms = ["conservative", "balanced", "aggressive", "prudent"]

    for name in tournament_arms:
        preset = TOURNAMENT_PRESETS[name]
        assert "stop_loss_trigger_usd_cents" not in preset, name
        assert "stop_loss_trigger_pct" not in preset, name


def test_tournament_preset_leaves_the_stop_to_the_operator_env():
    """A preset no longer moves the stop; the loaded defaults stand."""
    import os
    from unittest import mock

    for name in ["conservative", "balanced", "aggressive"]:
        with mock.patch.dict(os.environ, {"BOT_QUOTE_TOURNAMENT_PRESET": name}):
            cfg = load()
        assert cfg.stop_loss_mode == "NONE"
        assert cfg.drop_cents == 0.05
        assert cfg.drawdown_pct == 10.0
        assert cfg.bot_risk_circuit_max_drawdown_pct == 75.0


def test_preset_env_no_longer_emits_the_retired_stop_var():
    """The shadow child process is not handed a knob nothing reads."""
    from scripts.shadow_tournament import _preset_to_env

    env = _preset_to_env(
        {"name": "balanced", **TOURNAMENT_PRESETS["balanced"]}
    )

    assert "HUNTER_STOP_LOSS_TRIGGER_USD_CENTS" not in env
    assert "HUNTER_SINGLE_BUY_GRACE_SEC" not in env


def test_load_config_with_preset_and_env_overrides():
    with mock.patch.dict(os.environ, {"BOT_QUOTE_TOURNAMENT_PRESET": "prudent"}):
        cfg = load()
        assert cfg.dynamic_offset_enabled is True
        assert cfg.dynamic_offset_multiplier == 0.60
        assert cfg.dynamic_offset_min_cents == 2
        assert cfg.dynamic_offset_max_cents == 4

    with mock.patch.dict(os.environ, {"BOT_QUOTE_TOURNAMENT_PRESET": "balanced"}):
        cfg = load()
        assert cfg.dynamic_offset_enabled is True
        assert cfg.dynamic_offset_multiplier == 0.50
        assert cfg.dynamic_offset_min_cents == 2
        assert cfg.dynamic_offset_max_cents == 4

    # Env override on top of preset
    with mock.patch.dict(os.environ, {
        "BOT_QUOTE_TOURNAMENT_PRESET": "aggressive",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "3",
    }):
        cfg_custom = load()
        assert cfg_custom.dynamic_offset_enabled is True
        assert cfg_custom.dynamic_offset_multiplier == 0.25
        assert cfg_custom.dynamic_offset_min_cents == 2
        assert cfg_custom.dynamic_offset_max_cents == 3

    # Unknown preset raises ValueError
    with mock.patch.dict(os.environ, {"BOT_QUOTE_TOURNAMENT_PRESET": "unknown_arm"}):
        with pytest.raises(ValueError, match="BOT_QUOTE_TOURNAMENT_PRESET"):
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
    assert cfg.bot_quote_telemetry_range_cents == 5.5
    assert cfg.bot_quote_telemetry_velocity_measured_at == 1_788_000_123.0


def test_dynamic_offset_for_reads_canonical_names_without_warning():
    import warnings

    cfg = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True,
        bot_quote_spread_offset_multiplier=0.5,
        bot_quote_spread_offset_min_cents=1,
        bot_quote_spread_offset_max_cents=20,
        bot_quote_telemetry_range_cents=6.0,
        bot_quote_telemetry_velocity_measured_at=NOW - 60.0,
    )
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        offset, tag = dynamic_offset_for(cfg, now=NOW)
    assert offset == 0.03
    assert tag == "dynamic_3c"

    # Edge case: stale telemetry (> 900s)
    cfg_stale = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True,
        bot_quote_spread_static_offset_cents=0.020,
        bot_quote_spread_offset_max_age_sec=900.0,
        bot_quote_telemetry_range_cents=6.0,
        bot_quote_telemetry_velocity_measured_at=NOW - 1000.0,
    )
    with warnings.catch_warnings(record=True) as recorded_stale:
        warnings.simplefilter("always")
        offset_stale, tag_stale = dynamic_offset_for(cfg_stale, now=NOW)
    assert offset_stale == 0.020
    assert tag_stale == "static_stale_range"

    # Edge case: range 100.0 clamped to 20 cents
    cfg_clamp = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True,
        bot_quote_spread_offset_multiplier=0.5,
        bot_quote_spread_offset_min_cents=1,
        bot_quote_spread_offset_max_cents=20,
        bot_quote_telemetry_range_cents=100.0,
        bot_quote_telemetry_velocity_measured_at=NOW - 60.0,
    )
    with warnings.catch_warnings(record=True) as recorded_clamp:
        warnings.simplefilter("always")
        offset_clamp, tag_clamp = dynamic_offset_for(cfg_clamp, now=NOW)
    assert offset_clamp == 0.20
    assert tag_clamp == "dynamic_20c"

    all_records = list(recorded) + list(recorded_stale) + list(recorded_clamp)
    scoped_legacy = {
        "dynamic_offset_enabled",
        "dynamic_offset_multiplier",
        "dynamic_offset_min_cents",
        "dynamic_offset_max_cents",
        "dynamic_offset_max_age_sec",
        "range_cents",
        "velocity_measured_at",
        "reward_offset",
    }
    for w in all_records:
        if "quotes.py" in str(w.filename):
            assert not any(name in str(w.message) for name in scoped_legacy), f"Unexpected warning: {w.message}"



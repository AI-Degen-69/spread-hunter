"""#571: the picker's shipped values, pinned BEFORE the two config files were merged.

Written first, against the unmodified pair of classes, so that the refactor which
makes `core_brain.config` the single source has to reproduce every one of these
numbers rather than re-derive them. A guard that only compares the two classes to
each other would happily pass a move that changed both sides the same way; this
file is the other half of that proof -- the values themselves.

`SCORING_DEFAULTS` was generated once from the shipped `scoring.config.MakerConfig()`
(82 fields: the 74 shared with core plus the 8 picker-only bars). Do not hand-edit
it: a change here is a change to what the picker gates on, which belongs in its own
ticket (#572 / #573), not in a move.
"""
from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from core_brain.config import MakerConfig as CoreMakerConfig
from scoring import config as scoring_config
from scoring.config import MakerConfig as ScoringMakerConfig

SCORING_DEFAULTS: dict[str, object] = {
    "series_slug": "btc-up-or-down-5m",
    "bankroll_usd": 1000.0,
    "objective": "spread_capture",
    "reward_offset": 0.020,
    "max_skew_offset": 0.015,
    "min_reward_offset": 0.005,
    "max_naked_usd": 120.0,
    "enable_hard_blocks": True,
    "decided_price": 0.02,
    "max_book_spread": 0.06,
    "wide_book_trial": None,
    "min_book_depth_shares": 200.0,
    "emergency_hedge_pct": 0.8,
    "enable_emergency_hedge": True,
    "markout_horizons": (300.0, 3600.0, 21600.0, 900.0),
    "markout_min_sample": 8,
    "markout_fleet_min_sample": 25,
    "markout_widen_threshold": -0.005,
    "markout_catastrophic_threshold": -0.020,
    "widen_offset": 0.035,
    "gate_state": "NORMAL",
    "merge_max_loss_per_share": 0.01,
    "merge_velocity_hold_days": 30.0,
    "select_min_volume_24h_usd": 125_000.0,
    "select_min_top3_depth_usd": 500.0,
    "select_max_queue_minutes": 15.0,
    "enforce_max_queue_minutes": False,
    "select_min_top3_depth_usd_trial": None,
    "select_min_volume_24h_usd_trial": None,
    "select_min_volume_24h_usd_live": 35_000.0,
    "select_movement_window_sec": 1800.0,
    "select_min_movement_usd": 200.0,
    "select_velocity_gate_enabled": True,
    "select_min_activity_ratio": 0.0,
    "select_min_range_cents": 2.0,
    "select_volatility_window_sec": 7200.0,
    "select_spread_pct": 41.0 / 120.0,
    "select_max_days_to_resolve": 30.0,
    "rank_sample_window_sec": 1800.0,
    "rerank_interval_sec": 600.0,
    "scarcity_marginal_multiple": 2.0,
    "capital_scarce": False,
    "max_fleet_naked_usd": 400.0,
    "fleet_naked_usd": 0.0,
    "max_committed_usd": 1000.0,
    "committed_usd": 0.0,
    "fleet_posture": "NORMAL",
    "quote_both_sides": True,
    "ticks_below_ask": 1,
    "tick_size": 0.01,
    "price_tick": 0.01,
    "min_quote_shares": 50,
    "max_spread_from_mid": 0.045,
    "quote_shares": 120,
    "price_band_low": 0.10,
    "price_band_high": 0.90,
    "quote_window_pct": 0.40,
    "enforce_price_band": True,
    "enforce_quote_window": True,
    "coinflip_halfwidth": 0.20,
    "coinflip_size_cut": 0.55,
    "price_risk_widen": 0.010,
    "target_balance": 0.92,
    "max_pair_cost": 0.995,
    "max_fills_per_market": 25,
    "requote_interval_sec": 2.0,
    "poll_interval_sec": 1.0,
    "min_t_remaining_sec": 15.0,
    "max_cost_per_market": 400.0,
    "max_open_markets": 3,
    "enable_pairs_rule": True,
    "pairs_exit_window_sec": 900.0,
    "pairs_complete_gain_cents": 3.68,
    "pairs_exit_cost_cents": 3.67,
    "maker_fee": 0.0,
    "fee_rate": 0.07,
    "net_oneway_ms": 3.93,
    "cancel_venue_ack_ms": 150.0,
    "post_venue_accept_ms": 81.0,
    "pinned_condition_id": "",
    "market_title": "",
    "market_url": "",
}

# The five values that differ on purpose (CONSTRAINTS §2, verified 2026-10-18).
# `scoring` keeps its own number for each; the refactor moves WHERE they live,
# never WHAT they are.
CORE_DIFF_DEFAULTS: dict[str, object] = {
    "bankroll_usd": 100.0,
    "max_naked_usd": 6.0,
    "min_quote_shares": 5,
    "max_spread_from_mid": 0.15,
    "max_pair_cost": 0.99,
}

_MISSING = "<field gone>"


def _defaults(cfg) -> dict[str, object]:
    return {f.name: getattr(cfg, f.name) for f in dataclasses.fields(type(cfg))}


def _mismatches(cfg, expected: dict[str, object]) -> dict[str, tuple]:
    got = _defaults(cfg)
    return {
        name: (want, got.get(name, _MISSING))
        for name, want in expected.items()
        if got.get(name, _MISSING) != want
    }


def test_scoring_defaults_unchanged():
    """Every shipped picker default, byte for byte, after the merge."""
    assert not _mismatches(ScoringMakerConfig(), SCORING_DEFAULTS)


def test_every_pinned_field_still_exists():
    """A renamed or deleted field must fail here, not vanish from the pin."""
    names = {f.name for f in dataclasses.fields(ScoringMakerConfig)}

    assert set(SCORING_DEFAULTS) <= names, sorted(set(SCORING_DEFAULTS) - names)


def test_core_defaults_unchanged():
    """The live side of the five intentional differences."""
    assert not _mismatches(CoreMakerConfig(), CORE_DIFF_DEFAULTS)


def test_derived_values_unchanged():
    """The properties and helpers the merge could have silently re-pointed."""
    for cfg in (ScoringMakerConfig(), CoreMakerConfig()):
        assert cfg.tau_cancel_sec == (3.93 + 150.0) / 1000.0
        assert cfg.tau_post_sec == (3.93 + 81.0) / 1000.0
        assert cfg.max_skew == cfg.max_skew_offset == 0.015
        assert cfg.min_book_depth_sh == cfg.min_book_depth_shares == 200.0
        assert cfg.emergency_hedge_frac == cfg.emergency_hedge_pct == 0.8
        assert cfg.cancel_net_oneway_ms == cfg.net_oneway_ms == 3.93
        assert cfg.quote_window_frac == cfg.quote_window_pct == 0.40
        assert cfg.select_spread_frac == cfg.select_spread_pct == 41.0 / 120.0
        assert cfg.select_max_book_spread == 0.0205


def test_db_path_unchanged_under_every_env_state(monkeypatch):
    """`db_path()` resolves identically in both classes, in all three states.

    Only the default is rooted at `ROOT`; an explicit `HUNTER_DB` is taken
    verbatim, which is what the duplicated helper does in both files.
    """
    for name in ("HUNTER_DB", "SPREAD_HUNTER_DB"):
        monkeypatch.delenv(name, raising=False)
    assert scoring_config.MakerConfig().db_path() == scoring_config.ROOT / "hunter.db"

    monkeypatch.setenv("HUNTER_DB", "legacy.db")
    assert scoring_config.MakerConfig().db_path() == Path("legacy.db")

    monkeypatch.setenv("SPREAD_HUNTER_DB", "current.db")
    assert scoring_config.MakerConfig().db_path() == Path("current.db")

    # And the two classes agree in the state left standing, as they did before.
    assert scoring_config.MakerConfig().db_path() == CoreMakerConfig().db_path()


def test_loader_output_unchanged(monkeypatch):
    """`load()` with nothing set is the shipped class, not a re-derived one."""
    for name in ("HUNTER_MARKET", "HUNTER_WIDE_BOOK_TRIAL", "HUNTER_BANKROLL",
                 "SPREAD_HUNTER_BANKROLL", "HUNTER_DB", "SPREAD_HUNTER_DB"):
        monkeypatch.delenv(name, raising=False)

    loaded = scoring_config.load()

    assert not _mismatches(loaded, SCORING_DEFAULTS)
    assert not _mismatches(loaded, _defaults(ScoringMakerConfig()))


def test_scoring_config_is_frozen():
    cfg = ScoringMakerConfig()

    with pytest.raises(FrozenInstanceError):
        cfg.max_pair_cost = 0.5


# ============================================================================
# #652: the picker's eight filter bars, now owned by core.
#
# `SCORING_DEFAULTS` above is the values pin and stays untouched -- all 82 names
# still resolve, because the old spellings became alias-backed dataclass fields.
# These tests cover the half a values pin cannot see: that each override reaches
# the field core now owns, and that the old name still reads it back.
# ============================================================================

# The six overrides `scoring.config.load()` still honours: variable, the
# canonical field it must land on, a valid non-default raw value, and the parsed
# expectation. The two queue bars are absent on purpose -- they never had an
# override, and adding one is out of scope for #652.
PICKER_BAR_ENV = [
    ("HUNTER_MIN_MOVEMENT_USD", "filter_live_vel_min_usd", "777.0", 777.0),
    ("HUNTER_MOVEMENT_WINDOW_SEC", "filter_live_vel_lookback_sec", "900.0", 900.0),
    ("HUNTER_VELOCITY_GATE", "filter_live_gate_velocity_on", "off", False),
    ("HUNTER_MIN_ACTIVITY_RATIO", "filter_live_activity_min_ratio", "0.25", 0.25),
    ("HUNTER_MIN_RANGE_CENTS", "filter_live_move_min_range_cents", "3.5", 3.5),
    ("HUNTER_VOLATILITY_WINDOW_SEC", "filter_live_move_lookback_sec", "3600.0", 3600.0),
]

# The old picker name of each bar. Insertion order is the issue's order.
PICKER_BAR_OLD_NAMES: dict[str, str] = {
    "select_min_movement_usd": "filter_live_vel_min_usd",
    "select_movement_window_sec": "filter_live_vel_lookback_sec",
    "select_velocity_gate_enabled": "filter_live_gate_velocity_on",
    "select_min_activity_ratio": "filter_live_activity_min_ratio",
    "select_min_range_cents": "filter_live_move_min_range_cents",
    "select_volatility_window_sec": "filter_live_move_lookback_sec",
    "select_max_queue_minutes": "filter_std_queue_max_clear_min",
    "enforce_max_queue_minutes": "filter_std_gate_queue_max_on",
}

_OLD_NAME_OF = {canonical: old for old, canonical in PICKER_BAR_OLD_NAMES.items()}


def _clear_picker_bar_env(monkeypatch):
    """Clear every override these tests touch, so a shell value cannot leak in."""
    for var, _field, _raw, _expected in PICKER_BAR_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("var,field,raw,expected", PICKER_BAR_ENV)
def test_each_picker_bar_override_reaches_the_canonical_field(
        monkeypatch, var, field, raw, expected):
    """#652's silent no-op guard: the override must land on the field core owns.

    `_wrapped_init` maps a legacy keyword through `_LEGACY_TO_CANONICAL`; a name
    that is not in that table is stored as a stray attribute and the bar keeps
    its default. That is the failure this asserts against, end to end through
    the real `load()` and the real environment.
    """
    _clear_picker_bar_env(monkeypatch)
    monkeypatch.setenv(var, raw)

    loaded = scoring_config.load()

    assert getattr(loaded, field) == expected


@pytest.mark.parametrize("var,field,raw,expected", PICKER_BAR_ENV)
def test_each_picker_bar_override_is_readable_by_its_old_name(
        monkeypatch, var, field, raw, expected):
    """The dashboard's `getattr(scfg, "select_*", <fallback>)` must not fall back."""
    _clear_picker_bar_env(monkeypatch)
    monkeypatch.setenv(var, raw)

    loaded = scoring_config.load()

    with pytest.warns(DeprecationWarning):
        assert getattr(loaded, _OLD_NAME_OF[field]) == expected


def test_all_eight_picker_bars_read_the_canonical_value_by_their_old_name(monkeypatch):
    _clear_picker_bar_env(monkeypatch)

    loaded = scoring_config.load()

    for old, canonical in PICKER_BAR_OLD_NAMES.items():
        with pytest.warns(DeprecationWarning):
            assert getattr(loaded, old) == getattr(loaded, canonical), old


# Values that must be REFUSED rather than carried: not a number, not finite, or
# a non-positive window. Each keeps the shipped bar, which is the parsing
# behaviour #652 promised not to touch (the keys moved, nothing else).
PICKER_BAR_REFUSED_INPUT = [
    ("HUNTER_MIN_MOVEMENT_USD", "filter_live_vel_min_usd", "abc", 200.0),
    ("HUNTER_MIN_MOVEMENT_USD", "filter_live_vel_min_usd", "nan", 200.0),
    ("HUNTER_MOVEMENT_WINDOW_SEC", "filter_live_vel_lookback_sec", "0", 1800.0),
    ("HUNTER_MOVEMENT_WINDOW_SEC", "filter_live_vel_lookback_sec", "abc", 1800.0),
    ("HUNTER_MIN_RANGE_CENTS", "filter_live_move_min_range_cents", "-1", 2.0),
    ("HUNTER_MIN_RANGE_CENTS", "filter_live_move_min_range_cents", "inf", 2.0),
    ("HUNTER_VOLATILITY_WINDOW_SEC", "filter_live_move_lookback_sec", "0", 7200.0),
    ("HUNTER_MIN_ACTIVITY_RATIO", "filter_live_activity_min_ratio", "-0.5", 0.0),
    ("HUNTER_VELOCITY_GATE", "filter_live_gate_velocity_on", "no", False),
]


@pytest.mark.parametrize("var,field,raw,expected", PICKER_BAR_REFUSED_INPUT)
def test_a_refused_picker_bar_value_keeps_the_shipped_bar(
        monkeypatch, var, field, raw, expected):
    """A typo must not silently widen a gate, and a negative bar must not travel."""
    _clear_picker_bar_env(monkeypatch)
    monkeypatch.setenv(var, raw)

    loaded = scoring_config.load()

    assert getattr(loaded, field) == expected


def test_the_eight_picker_bars_keep_their_shipped_defaults(monkeypatch):
    """The values the issue pins, read off a loaded config with nothing set."""
    _clear_picker_bar_env(monkeypatch)

    loaded = scoring_config.load()

    shipped = [getattr(loaded, canonical) for canonical in PICKER_BAR_OLD_NAMES.values()]

    assert shipped == [200.0, 1800.0, True, 0.0, 2.0, 7200.0, 15.0, False]

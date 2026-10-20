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

from core_brain import config as core_config
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


# ============================================================================
# #653: canonical `FILTER_*` environment variable overrides & precedence
# ============================================================================

SCREENER_CANONICAL_ENV = [
    # (canonical_var, legacy_var, field, canonical_raw, canonical_val, legacy_raw, legacy_val, default_val)
    ("FILTER_LIVE_VEL_MIN_USD", "HUNTER_MIN_MOVEMENT_USD", "filter_live_vel_min_usd", "888.0", 888.0, "777.0", 777.0, 200.0),
    ("FILTER_LIVE_VEL_LOOKBACK_SEC", "HUNTER_MOVEMENT_WINDOW_SEC", "filter_live_vel_lookback_sec", "600.0", 600.0, "900.0", 900.0, 1800.0),
    ("FILTER_LIVE_GATE_VELOCITY_ON", "HUNTER_VELOCITY_GATE", "filter_live_gate_velocity_on", "false", False, "true", True, True),
    ("FILTER_LIVE_MOVE_MIN_RANGE_CENTS", "HUNTER_MIN_RANGE_CENTS", "filter_live_move_min_range_cents", "4.5", 4.5, "3.5", 3.5, 2.0),
    ("FILTER_LIVE_MOVE_LOOKBACK_SEC", "HUNTER_VOLATILITY_WINDOW_SEC", "filter_live_move_lookback_sec", "5400.0", 5400.0, "3600.0", 3600.0, 7200.0),
    ("FILTER_LIVE_ACTIVITY_MIN_RATIO", "HUNTER_MIN_ACTIVITY_RATIO", "filter_live_activity_min_ratio", "0.35", 0.35, "0.25", 0.25, 0.0),
    ("FILTER_LIVE_VOL_MIN_24H_USD", None, "filter_live_vol_min_24h_usd", "50000.0", 50000.0, None, None, 35_000.0),
    ("FILTER_STD_VOL_MIN_24H_USD", "HUNTER_MIN_VOLUME_24H_USD", "filter_std_vol_min_24h_usd", "250000.0", 250000.0, "150000.0", 150000.0, 125_000.0),
    ("FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL", "HUNTER_DEPTH_TRIAL_USD", "filter_common_vol_min_top3_depth_usd_trial", "300.0", 300.0, "250.0", 250.0, None),
    ("FILTER_STD_VOL_MIN_24H_USD_TRIAL", "HUNTER_VOLUME_TRIAL_USD", "filter_std_vol_min_24h_usd_trial", "75000.0", 75000.0, "50000.0", 50000.0, None),
]


def _clear_screener_canonical_env(monkeypatch):
    for can_var, leg_var, *_ in SCREENER_CANONICAL_ENV:
        monkeypatch.delenv(can_var, raising=False)
        if leg_var is not None:
            monkeypatch.delenv(leg_var, raising=False)


@pytest.mark.parametrize(
    "can_var,leg_var,field,can_raw,can_val,leg_raw,leg_val,default_val",
    SCREENER_CANONICAL_ENV,
)
def test_canonical_filter_env_overrides_field_and_wins_over_legacy(
        monkeypatch, can_var, leg_var, field, can_raw, can_val, leg_raw, leg_val, default_val):
    """#653: each canonical `FILTER_*` overrides its field and beats `HUNTER_*`."""
    _clear_screener_canonical_env(monkeypatch)

    # 1. Unset -> shipped default
    assert getattr(scoring_config.load(), field) == default_val

    # 2. Canonical alone -> canonical value
    monkeypatch.setenv(can_var, can_raw)
    assert getattr(scoring_config.load(), field) == can_val

    if leg_var is not None:
        # 3. Both set -> canonical wins over legacy
        monkeypatch.setenv(leg_var, leg_raw)
        assert getattr(scoring_config.load(), field) == can_val

        # 4. Blank or whitespace canonical -> falls through to legacy
        for blank in ("", "   "):
            monkeypatch.setenv(can_var, blank)
            assert getattr(scoring_config.load(), field) == leg_val

        # 5. Legacy alone -> legacy value still works
        monkeypatch.delenv(can_var, raising=False)
        assert getattr(scoring_config.load(), field) == leg_val


# ============================================================================
# #674: canonical `BOT_*` execution environment variable overrides & precedence
# ============================================================================

EXECUTION_CANONICAL_ENV = [
    # (canonical_var, legacy_vars, field, can_raw, can_val, leg_raw, leg_val, default_val, needs_pin, needs_rehearsal)
    # An empty legacy tuple means the variable has no alias left, so its row
    # answers the canonical and blank-canonical questions only.
    ("BOT_MARKET_PINNED_CONDITION_ID", ("HUNTER_MARKET",), "bot_market_pinned_condition_id", "0xcan_pinned", "0xcan_pinned", "0xleg_pinned", "0xleg_pinned", "", False, False),
    ("BOT_MARKET_TITLE", ("HUNTER_TITLE",), "bot_market_title", "Canonical Title", "Canonical Title", "Legacy Title", "Legacy Title", "", True, False),
    ("BOT_MARKET_URL", ("HUNTER_URL",), "bot_market_url", "https://polymarket.com/can", "https://polymarket.com/can", "https://polymarket.com/leg", "https://polymarket.com/leg", "", True, False),
    ("BOT_QUOTE_SIZE_MIN_SHARES", ("HUNTER_MIN_QUOTE_SHARES", "HUNTER_MIN_SIZE"), "bot_quote_size_min_shares", "200", 200, "150", 150, 50, True, False),
    ("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", (), "bot_quote_book_max_spread_from_mid", "25", 0.25, None, None, 0.045, True, False),
    ("BOT_QUOTE_GRID_TICK_SIZE_USD", ("HUNTER_PRICE_TICK", "HUNTER_TICK"), "bot_quote_grid_tick_size_usd", "0.005", 0.005, "0.02", 0.02, 0.01, True, False),
    ("BOT_QUOTE_WIDE_BOOK_TRIAL", ("HUNTER_WIDE_BOOK_TRIAL",), "bot_quote_wide_book_trial", "0.12", 0.12, "0.15", 0.15, None, False, True),
    ("BOT_RISK_GATE_PAIRS_RULE_ON", ("HUNTER_PAIRS_RULE",), "bot_risk_gate_pairs_rule_on", "off", False, "1", True, True, False, False),
    ("BOT_LATENCY_NET_ONEWAY_MS", ("HUNTER_NET_ONEWAY_MS", "HUNTER_CANCEL_NET_ONEWAY_MS"), "bot_latency_net_oneway_ms", "9.5", 9.5, "7.5", 7.5, 3.93, False, False),
    ("BOT_LATENCY_CANCEL_VENUE_ACK_MS", ("HUNTER_CANCEL_VENUE_ACK_MS",), "bot_latency_cancel_venue_ack_ms", "210.0", 210.0, "190.0", 190.0, 150.0, False, False),
    ("BOT_LATENCY_POST_VENUE_ACCEPT_MS", ("HUNTER_POST_VENUE_ACCEPT_MS",), "bot_latency_post_venue_accept_ms", "95.0", 95.0, "85.0", 85.0, 81.0, False, False),
    ("BOT_QUOTE_BANK_BASELINE_USD", ("SPREAD_HUNTER_BANKROLL", "HUNTER_BANKROLL"), "bot_quote_bank_baseline_usd", "2500.0", 2500.0, "1500.0", 1500.0, 1000.0, False, False),
]


def _clear_execution_env(monkeypatch):
    all_vars = [
        "BOT_MARKET_PINNED_CONDITION_ID", "HUNTER_MARKET",
        "BOT_MARKET_TITLE", "HUNTER_TITLE",
        "BOT_MARKET_URL", "HUNTER_URL",
        "BOT_QUOTE_SIZE_MIN_SHARES", "HUNTER_MIN_QUOTE_SHARES", "HUNTER_MIN_SIZE",
        "BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID",
        "BOT_QUOTE_GRID_TICK_SIZE_USD", "HUNTER_PRICE_TICK", "HUNTER_TICK",
        "BOT_QUOTE_WIDE_BOOK_TRIAL", "HUNTER_WIDE_BOOK_TRIAL",
        "BOT_RISK_GATE_PAIRS_RULE_ON", "HUNTER_PAIRS_RULE",
        "BOT_LATENCY_NET_ONEWAY_MS", "HUNTER_NET_ONEWAY_MS", "HUNTER_CANCEL_NET_ONEWAY_MS",
        "BOT_LATENCY_CANCEL_VENUE_ACK_MS", "HUNTER_CANCEL_VENUE_ACK_MS",
        "BOT_LATENCY_POST_VENUE_ACCEPT_MS", "HUNTER_POST_VENUE_ACCEPT_MS",
        "BOT_QUOTE_BANK_BASELINE_USD", "SPREAD_HUNTER_BANKROLL", "HUNTER_BANKROLL",
    ]
    for v in all_vars:
        monkeypatch.delenv(v, raising=False)


@pytest.mark.parametrize(
    "can_var,leg_vars,field,can_raw,can_val,leg_raw,leg_val,default_val,needs_pin,needs_rehearsal",
    EXECUTION_CANONICAL_ENV,
)
def test_canonical_execution_env_overrides_field_and_wins_over_legacy(
        monkeypatch, can_var, leg_vars, field, can_raw, can_val, leg_raw, leg_val, default_val, needs_pin, needs_rehearsal):
    """#674: each canonical BOT_* execution variable overrides its field and beats legacy HUNTER_*."""
    _clear_execution_env(monkeypatch)
    if needs_rehearsal:
        monkeypatch.setattr("core_brain.rehearsal.is_rehearsal", lambda: True)

    def _setup_pin():
        if needs_pin:
            monkeypatch.setenv("BOT_MARKET_PINNED_CONDITION_ID", "0xpinned_for_test")
            monkeypatch.setenv("HUNTER_MARKET", "0xpinned_for_test")

    # 1. Unset -> shipped default
    _setup_pin()
    assert getattr(scoring_config.load(), field) == default_val

    # 2. Canonical alone -> canonical value
    monkeypatch.setenv(can_var, can_raw)
    assert getattr(scoring_config.load(), field) == can_val

    # 3. Both set -> canonical wins over legacy
    for leg_var in leg_vars:
        monkeypatch.setenv(leg_var, leg_raw)
    assert getattr(scoring_config.load(), field) == can_val

    # 4. Blank or whitespace canonical -> falls through to legacy, and to the
    #    shipped default for a variable that has no alias left (#706)
    for blank in ("", "   "):
        monkeypatch.setenv(can_var, blank)
        assert getattr(scoring_config.load(), field) == (leg_val if leg_vars else default_val)

    # 5. Each legacy var alone with newer cleared -> legacy value still works
    if leg_vars:
        monkeypatch.delenv(can_var, raising=False)
        for leg_var in leg_vars:
            for other in leg_vars:
                monkeypatch.delenv(other, raising=False)
            monkeypatch.setenv(leg_var, leg_raw)
            assert getattr(scoring_config.load(), field) == leg_val


def test_execution_coupling_overrides(monkeypatch):
    """#674: Coupling tests for size (quote_shares floor), bankroll (max_committed_usd), and trial (widen max_spread_from_mid)."""
    _clear_execution_env(monkeypatch)
    monkeypatch.setenv("BOT_MARKET_PINNED_CONDITION_ID", "0xpinned")
    monkeypatch.setenv("HUNTER_MARKET", "0xpinned")

    # 1. Size override raises quote_shares to max(n, 120)
    monkeypatch.setenv("BOT_QUOTE_SIZE_MIN_SHARES", "200")
    cfg = scoring_config.load()
    assert cfg.min_quote_shares == 200
    assert cfg.bot_quote_size_shares == 200

    monkeypatch.setenv("BOT_QUOTE_SIZE_MIN_SHARES", "50")
    cfg = scoring_config.load()
    assert cfg.min_quote_shares == 50
    assert cfg.bot_quote_size_shares == 120

    # 2. Bankroll override sets bankroll_usd and max_committed_usd
    monkeypatch.setenv("BOT_QUOTE_BANK_BASELINE_USD", "250.0")
    cfg = scoring_config.load()
    assert cfg.bankroll_usd == 250.0
    assert cfg.bot_risk_exposure_max_committed_usd == 250.0

    # 3. Wide-book trial override only widens max_spread_from_mid
    monkeypatch.setattr("core_brain.rehearsal.is_rehearsal", lambda: True)
    monkeypatch.setenv("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", "5")  # 5 cents = 0.05
    monkeypatch.setenv("BOT_QUOTE_WIDE_BOOK_TRIAL", "0.12")
    cfg = scoring_config.load()
    assert cfg.bot_quote_wide_book_trial == 0.12
    assert cfg.max_book_spread == 0.12
    assert cfg.max_spread_from_mid == 0.12

    # Smaller trial does not lower max_spread_from_mid
    monkeypatch.setenv("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", "20")  # 20 cents = 0.20
    monkeypatch.setenv("BOT_QUOTE_WIDE_BOOK_TRIAL", "0.12")
    cfg = scoring_config.load()
    assert cfg.max_spread_from_mid == 0.20


# ============================================================================
# #690: the spread window is read on EVERY run, pinned or not (N2 of #674)
# ============================================================================


def test_unpinned_runs_read_the_spread_window_from_the_environment(monkeypatch):
    """#690: without a pin the picker reads the same override the engine does.

    The whole read used to live inside `if cid:`, so an unpinned ranker run kept
    the picker's 4.5c default while the engine honoured the operator's setting.
    """
    _clear_execution_env(monkeypatch)

    # Nothing set: the picker's own shipped window (4.5c), never core's 15c.
    assert scoring_config.load().max_spread_from_mid == 0.045

    monkeypatch.setenv("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", "25")
    assert scoring_config.load().max_spread_from_mid == 0.25

    # Blank is unset on this path too, so the picker's own window stands.
    monkeypatch.setenv("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", "   ")
    assert scoring_config.load().max_spread_from_mid == 0.045


def test_unpinned_bad_spread_values_are_inactive_not_enforced(monkeypatch):
    """#707 narrowed this: only a NON-NUMBER falls back now, never a bad number.

    #690 made unpinned runs tolerant so a ranker that starts cleanly today cannot
    start crashing on a typo. `0` and `-5` used to ride along with that tolerance
    and were silently dropped; #707 makes them rejections on both paths, because a
    value the operator set that is quietly ignored reads as "enforced" while
    permitting anything. A blank name still counts as unset.
    """
    _clear_execution_env(monkeypatch)
    for raw in ("", "wide"):
        monkeypatch.setenv("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", raw)
        assert scoring_config.load().max_spread_from_mid == 0.045, raw


def test_unpinned_scoring_and_engine_load_the_same_window(monkeypatch):
    """#690: parity with `core_brain.config.load()`, including its quirks.

    An unusable value is inactive on both sides, so each keeps its own default --
    the assertion there is "unchanged", not "equal", because the two defaults
    differ by design. `inf` was in the matrix in #690 because the engine's
    tolerant read applied any `cents > 0`; #707 retired that case and moved it to
    the rejection tests below.
    """
    _clear_execution_env(monkeypatch)

    for raw in ("22", "7.5"):
        monkeypatch.setenv("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", raw)
        expected = float(raw) / 100.0
        assert scoring_config.load().max_spread_from_mid == pytest.approx(expected)
        assert core_config.load().max_spread_from_mid == pytest.approx(expected)

    monkeypatch.setenv("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", "wide")
    assert scoring_config.load().max_spread_from_mid == 0.045
    assert core_config.load().max_spread_from_mid == 0.15


# ============================================================================
# #707: the spread window is validated -- 1..99 cents, never inf or nan
# ============================================================================


_BOTH_LOADERS = pytest.mark.parametrize(
    "mod", [core_config, scoring_config], ids=["core_brain", "scoring"])
_BOTH_PATHS = pytest.mark.parametrize("pinned", [False, True], ids=["unpinned", "pinned"])


def _spread_window(mod, monkeypatch, raw, *, pinned, var="BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID"):
    """Load `mod` with the window set through `var`, blank raw meaning unset.

    The value is in CENTS: `1` is `0.01`, `99` is `0.99`. The rejection names the
    variable the operator actually set, so a caller checking a name that is not the
    canonical one passes it in `var`.
    """
    _clear_execution_env(monkeypatch)
    if pinned:
        monkeypatch.setenv("BOT_MARKET_PINNED_CONDITION_ID", "0xpinned")
    if raw:
        monkeypatch.setenv(var, raw)
    return mod.load()


@_BOTH_LOADERS
@_BOTH_PATHS
@pytest.mark.parametrize(
    "raw,expected",
    [("1", 0.01), ("99", 0.99), ("7.5", 0.075), ("22", 0.22)],
    ids=["one_cent", "ninety_nine", "half_cent", "twenty_two"],
)
def test_spread_window_accepts_one_to_ninety_nine_cents(mod, monkeypatch, pinned, raw, expected):
    """#707: both loaders, pinned and unpinned, store the ticket's 0.01..0.99 bounds."""
    assert _spread_window(mod, monkeypatch, raw, pinned=pinned).max_spread_from_mid \
        == pytest.approx(expected)


@_BOTH_LOADERS
@_BOTH_PATHS
@pytest.mark.parametrize(
    "raw", ["0", "100", "99.5", "inf", "nan", "-inf", "-5", "0.5"],
    ids=["zero", "one_dollar", "just_over", "pos_inf", "nan", "neg_inf", "negative", "half_a_cent"],
)
def test_spread_window_rejects_bad_numbers_on_both_paths(mod, monkeypatch, pinned, raw):
    """#707: a bad NUMBER is rejected, never clamped and never silently ignored.

    `0` and negatives used to be dropped by the tolerant read while `inf` set an
    infinite window -- the panel then read "enforced" while anything was allowed.
    `99.5` is here because it is the value that proves the bound is on CENTS: as a
    stored price fraction it would be a legal 0.995.
    """
    with pytest.raises(ValueError, match="BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID"):
        _spread_window(mod, monkeypatch, raw, pinned=pinned)


@_BOTH_LOADERS
def test_spread_window_rejection_names_the_variable_and_the_value(mod, monkeypatch):
    """#707: the message is what #709 can surface, so it carries both."""
    with pytest.raises(ValueError) as exc:
        _spread_window(mod, monkeypatch, "inf", pinned=False)
    assert str(exc.value) == "BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID='inf' is not finite"

    with pytest.raises(ValueError) as exc:
        _spread_window(mod, monkeypatch, "100", pinned=False)
    assert "BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID='100' is outside 1..99" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        _spread_window(mod, monkeypatch, "0", pinned=True)
    assert "BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID='0' is outside 1..99" in str(exc.value)


@_BOTH_LOADERS
def test_a_typo_in_the_spread_window_keeps_its_per_path_behaviour(mod, monkeypatch):
    """#690's rule survives #707: unpinned a typo is inactive, pinned it still raises.

    Only non-numbers are tolerated, and only unpinned. The pinned load has raised
    on a typo since the beginning, and it now names the variable while doing it.
    """
    default = 0.15 if mod is core_config else 0.045
    assert _spread_window(mod, monkeypatch, "wide", pinned=False).max_spread_from_mid == default

    with pytest.raises(ValueError, match="BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID='wide' is not a number"):
        _spread_window(mod, monkeypatch, "wide", pinned=True)


@pytest.mark.parametrize(
    "oldest_var,newer_var,canonical_var,val_raw,warn_text",
    [
        (
            "HUNTER_MIN_SIZE",
            "HUNTER_MIN_QUOTE_SHARES",
            "BOT_QUOTE_SIZE_MIN_SHARES",
            "100",
            "HUNTER_MIN_SIZE is deprecated (#632); set BOT_QUOTE_SIZE_MIN_SHARES instead.",
        ),
        (
            "HUNTER_TICK",
            "HUNTER_PRICE_TICK",
            "BOT_QUOTE_GRID_TICK_SIZE_USD",
            "0.02",
            "HUNTER_TICK is deprecated (#632); set BOT_QUOTE_GRID_TICK_SIZE_USD instead.",
        ),
    ],
)
def test_execution_legacy_deprecation_warnings(
        monkeypatch, caplog, oldest_var, newer_var, canonical_var, val_raw, warn_text):
    """#674: Oldest legacy variable emits #632 warning only when it is the winning source."""
    import logging

    _clear_execution_env(monkeypatch)
    monkeypatch.setenv("BOT_MARKET_PINNED_CONDITION_ID", "0xpinned")

    # 1. Oldest legacy alone -> warning emitted
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        monkeypatch.setenv(oldest_var, val_raw)
        scoring_config.load()
    assert warn_text in caplog.text

    # 2. Canonical set alongside -> no warning emitted (canonical wins)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        monkeypatch.setenv(canonical_var, val_raw)
        scoring_config.load()
    assert warn_text not in caplog.text

    # 3. Newer legacy alone -> no warning emitted
    monkeypatch.delenv(canonical_var, raising=False)
    monkeypatch.delenv(oldest_var, raising=False)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        monkeypatch.setenv(newer_var, val_raw)
        scoring_config.load()
    assert warn_text not in caplog.text


def test_blank_canonical_fallthrough_fires_legacy_warning(monkeypatch, caplog):
    """#674: Blank canonical falls through to oldest legacy and still emits the #632 warning."""
    import logging

    _clear_execution_env(monkeypatch)
    monkeypatch.setenv("BOT_MARKET_PINNED_CONDITION_ID", "0xpinned")
    monkeypatch.setenv("BOT_QUOTE_SIZE_MIN_SHARES", "   ")
    monkeypatch.setenv("HUNTER_MIN_SIZE", "200")

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        cfg = scoring_config.load()

    assert cfg.min_quote_shares == 200
    assert "HUNTER_MIN_SIZE is deprecated (#632)" in caplog.text


def test_wide_book_trial_refusal_names_variable(monkeypatch):
    """#674: Wide-book trial refusal outside rehearsal names the variable that was set."""
    _clear_execution_env(monkeypatch)
    monkeypatch.setattr("core_brain.rehearsal.is_rehearsal", lambda: False)

    # 1. Canonical variable set outside rehearsal
    monkeypatch.setenv("BOT_QUOTE_WIDE_BOOK_TRIAL", "0.12")
    with pytest.raises(ValueError) as exc1:
        scoring_config.load()
    assert "BOT_QUOTE_WIDE_BOOK_TRIAL widens max_book_spread" in str(exc1.value)

    # 2. Legacy variable set outside rehearsal
    monkeypatch.delenv("BOT_QUOTE_WIDE_BOOK_TRIAL", raising=False)
    monkeypatch.setenv("HUNTER_WIDE_BOOK_TRIAL", "0.12")
    with pytest.raises(ValueError) as exc2:
        scoring_config.load()
    assert "HUNTER_WIDE_BOOK_TRIAL widens max_book_spread" in str(exc2.value)

    # 3. Inside rehearsal, value exceeding 0.30 is rejected
    monkeypatch.setattr("core_brain.rehearsal.is_rehearsal", lambda: True)
    monkeypatch.setenv("BOT_QUOTE_WIDE_BOOK_TRIAL", "0.31")
    with pytest.raises(ValueError) as exc3:
        scoring_config.load()
    assert "is outside 0..0.3" in str(exc3.value)


@pytest.mark.parametrize("bad_raw", ["abc", "nan", "inf", "-1", ""])
def test_invalid_live_volume_override_keeps_shipped_default(monkeypatch, bad_raw):
    """`FILTER_LIVE_VOL_MIN_24H_USD` refuses non-finite or negative input."""
    _clear_screener_canonical_env(monkeypatch)
    monkeypatch.setenv("FILTER_LIVE_VOL_MIN_24H_USD", bad_raw)

    assert scoring_config.load().filter_live_vol_min_24h_usd == 35_000.0


@pytest.mark.parametrize(
    "can_var",
    ["FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL", "FILTER_STD_VOL_MIN_24H_USD_TRIAL"],
)
@pytest.mark.parametrize("bad_raw", ["abc", "nan", "inf", "-1"])
def test_invalid_trial_override_keeps_shipped_default(monkeypatch, can_var, bad_raw):
    """Trial knobs refuse non-numeric, non-finite, or negative input instead of crashing."""
    _clear_screener_canonical_env(monkeypatch)
    monkeypatch.setenv(can_var, bad_raw)

    cfg = scoring_config.load()
    assert cfg.filter_common_vol_min_top3_depth_usd_trial is None
    assert cfg.filter_std_vol_min_24h_usd_trial is None


@pytest.mark.parametrize(
    "var_name",
    [
        "FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL",
        "HUNTER_DEPTH_TRIAL_USD",
        "FILTER_STD_VOL_MIN_24H_USD_TRIAL",
        "HUNTER_VOLUME_TRIAL_USD",
    ],
)
@pytest.mark.parametrize("bad_raw", ["abc", "nan", "inf", "-1"])
def test_engine_invalid_trial_override_raises_named_error(monkeypatch, var_name, bad_raw):
    """#675: Engine fails loud on invalid trial values, with ValueError naming the variable."""
    _clear_screener_canonical_env(monkeypatch)
    monkeypatch.setenv(var_name, bad_raw)

    with pytest.raises(ValueError) as exc_info:
        CoreMakerConfig.from_env()

    assert var_name in str(exc_info.value)


@pytest.mark.parametrize(
    "can_var,leg_var,field",
    [
        (
            "FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL",
            "HUNTER_DEPTH_TRIAL_USD",
            "select_min_top3_depth_usd_trial",
        ),
        (
            "FILTER_STD_VOL_MIN_24H_USD_TRIAL",
            "HUNTER_VOLUME_TRIAL_USD",
            "select_min_volume_24h_usd_trial",
        ),
    ],
)
def test_engine_valid_trial_override_populates_field(monkeypatch, can_var, leg_var, field):
    """#675: Valid positive finite floats populate engine trial fields; unset/empty stays None."""
    _clear_screener_canonical_env(monkeypatch)

    # 1. Unset -> None
    cfg = CoreMakerConfig.from_env()
    assert getattr(cfg, field) is None

    # 2. Empty string -> None
    monkeypatch.setenv(can_var, "   ")
    cfg = CoreMakerConfig.from_env()
    assert getattr(cfg, field) is None

    # 3. Canonical valid float -> parsed correctly
    monkeypatch.setenv(can_var, "1234.5")
    cfg = CoreMakerConfig.from_env()
    assert getattr(cfg, field) == 1234.5

    # 4. Canonical beats legacy
    monkeypatch.setenv(leg_var, "999.0")
    cfg = CoreMakerConfig.from_env()
    assert getattr(cfg, field) == 1234.5

    # 5. Legacy alone works
    monkeypatch.delenv(can_var, raising=False)
    cfg = CoreMakerConfig.from_env()
    assert getattr(cfg, field) == 999.0


def test_env_example_documents_only_real_canonical_variables():
    """Every `FILTER_*` and `BOT_*` variable in `.env.example` is read by a loader."""
    import re
    from core_brain import config as core_config

    repo_root = Path(scoring_config.__file__).resolve().parent.parent
    env_example = (repo_root / ".env.example").read_text(encoding="utf-8")
    scoring_src = Path(scoring_config.__file__).read_text(encoding="utf-8")
    core_src = Path(core_config.__file__).read_text(encoding="utf-8")

    documented = re.findall(r"^#\s*((?:FILTER|BOT)_[A-Z0-9_]+)=", env_example, re.MULTILINE)
    assert len(documented) >= 10, "expected canonical FILTER_* and BOT_* entries in .env.example"

    unread = [
        var for var in documented
        if f'"{var}"' not in scoring_src and f'"{var}"' not in core_src
    ]
    assert not unread, f".env.example documents variables no loader reads: {unread}"


# ============================================================================
# #706: `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` is the only accepted name
# ============================================================================


@_BOTH_LOADERS
@_BOTH_PATHS
@pytest.mark.parametrize(
    "ghost", ["HUNTER_QUOTE_BOOK_MAX_SPREAD_FROM_MID", "BOT_QUOTE_BOOK_MAX_SPREAD"],
    ids=["hunter_prefixed", "missing_from_mid"],
)
def test_a_name_that_is_not_the_canonical_one_leaves_the_window_shipped(
        mod, monkeypatch, pinned, ghost):
    """#706: an unknown name is inert, never an error and never a fallback.

    The loader cannot tell an operator's stale shell from a typo, and the
    ticket's own acceptance criterion is that a name it does not publish has
    zero effect -- so the shipped window stands and nothing is raised.
    """
    shipped = 0.15 if mod is core_config else 0.045
    assert _spread_window(mod, monkeypatch, "40", pinned=pinned, var=ghost) \
        .max_spread_from_mid == shipped


# ============================================================================
# #708: one description of the quote window, in both config modules
# ============================================================================


def _quote_window_description(module) -> list[str]:
    """The comment block that documents `max_spread_from_mid`, line by line."""
    lines = Path(module.__file__).read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.strip().startswith("# THE QUOTE WINDOW.")), None)
    if start is None:
        return []
    block = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped.startswith("#"):
            break
        block.append(stripped)
    return block


def test_both_config_modules_describe_the_quote_window_identically():
    """#708: one field, one description.

    The field was documented three different ways (#690 O4): a "4.5c maximum
    offset from mid" in the picker, a venue rebate-qualification window in the
    wide-book trial test, and a resting-offset ceiling in the engine. A comment
    fix with no guard drifts again, so the two config modules are compared
    against each other here.
    """
    scoring_block = _quote_window_description(scoring_config)
    core_block = _quote_window_description(core_config)

    assert scoring_block, "scoring/config.py lost the quote-window description"
    assert core_block, "core_brain/config.py lost the quote-window description"
    assert scoring_block == core_block, (
        "the two config modules describe max_spread_from_mid differently:\n"
        f"scoring: {scoring_block}\ncore: {core_block}"
    )

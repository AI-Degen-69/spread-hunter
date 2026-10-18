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

"""Config for the spread hunter maker.

THE OBJECTIVE IS SPREAD CAPTURE: buy UP+DOWN below $1.00, merge the pair back to
collateral, keep the discount. Graduated spread markets carry $0.00 maker rewards;
income derives strictly from merge spread capture ($1.00 - pair cost). `objective` below names a
PRICING MODE, not the objective -- read its comment before trusting the literal.
See AGENTS.md for the full statement and the universe rules.

Quoting parameters (price band, offsets, balance target, fill caps) are derived
from 56,768 of @powerwinner's BTC/ETH 5-min fills over 2026-07-14..21 (2,970
markets). That is where the NUMBERS came from -- it is not where the bot
trades. The live universe is the Market Filter's graduated list in
runtime/markets.json.
"""
from __future__ import annotations

import logging
import math
import os
import warnings
from inspect import getattr_static
from dataclasses import dataclass, fields, Field, MISSING, _FIELD
from pathlib import Path

from core_brain import rehearsal
from core_brain.single_leg_lifecycle import (
    DROP_CENTS_MAX,
    DROP_CENTS_MIN,
    STOP_LOSS_MODES,
)

ROOT = Path(__file__).resolve().parent.parent

# --- CANONICAL / LEGACY ATTRIBUTE MAPPINGS ---
_LEGACY_TO_CANONICAL: dict[str, str] = {
    # Screener
    "select_min_volume_24h_usd": "filter_std_vol_min_24h_usd",
    "select_min_volume_24h_usd_live": "filter_live_vol_min_24h_usd",
    "select_min_volume_24h_usd_trial": "filter_std_vol_min_24h_usd_trial",
    "select_min_top3_depth_usd": "filter_common_vol_min_top3_depth_usd",
    "select_min_top3_depth_usd_trial": "filter_common_vol_min_top3_depth_usd_trial",
    "select_max_days_to_resolve": "filter_std_time_max_days_to_resolve",
    "rank_sample_window_sec": "filter_std_rank_sample_window_sec",
    "rerank_interval_sec": "filter_std_rerank_interval_sec",
    # Quoting
    "quote_both_sides": "bot_quote_both_sides_on",
    "ticks_below_ask": "bot_quote_ticks_below_ask",
    "tick_size": "bot_quote_tick_size",
    "price_tick": "bot_quote_grid_tick_size_usd",
    "apply_widen_gate": "bot_quote_gate_widen_on",
    "size_mode": "bot_quote_size_mode",
    "quote_shares": "bot_quote_size_shares",
    "max_quote_shares": "bot_quote_size_max_shares",
    "couple_risk_pct": "bot_quote_alloc_couple_pct",
    "min_couple_usd": "bot_quote_alloc_couple_min_usd",
    "waive_attenuation_below_floor": "bot_quote_waive_attenuation_below_floor_on",
    "require_two_sided_when_flat": "bot_quote_require_two_sided_when_flat_on",
    "price_band_low": "bot_quote_price_band_min_cents",
    "price_band_high": "bot_quote_price_band_max_cents",
    "quote_window_pct": "bot_quote_window_max_pct",
    "enforce_price_band": "bot_quote_gate_price_band_on",
    "enforce_quote_window": "bot_quote_gate_quote_window_on",
    "coinflip_halfwidth": "bot_quote_coinflip_halfwidth",
    "coinflip_size_cut": "bot_quote_coinflip_size_cut",
    "price_risk_widen": "bot_quote_price_risk_widen",
    "min_book_depth_shares": "bot_quote_book_min_depth_shares",
    "wide_book_trial": "bot_quote_wide_book_trial",
    "reward_offset": "bot_quote_spread_static_offset_cents",
    "dynamic_offset_enabled": "bot_quote_gate_dynamic_offset_on",
    "dynamic_offset_multiplier": "bot_quote_spread_offset_multiplier",
    "dynamic_offset_min_cents": "bot_quote_spread_offset_min_cents",
    "dynamic_offset_max_cents": "bot_quote_spread_offset_max_cents",
    "dynamic_offset_max_age_sec": "bot_quote_spread_offset_max_age_sec",
    "range_cents": "bot_quote_telemetry_range_cents",
    "velocity_measured_at": "bot_quote_telemetry_velocity_measured_at",
    "naked_risk_pct": "bot_risk_exposure_max_naked_pct",
    "order_risk_pct": "bot_risk_exposure_max_order_pct",
    "bankroll_ceiling_pct": "bot_risk_exposure_bankroll_ceiling_pct",
    "max_order_usd": "bot_risk_exposure_max_order_usd",
    "max_total_usd": "bot_risk_exposure_max_total_usd",
    # Position
    "objective": "bot_pos_objective",
    "target_balance": "bot_pos_skew_target_balance_ratio",
    "max_skew_offset": "bot_pos_skew_max_offset_cents",
    "min_reward_offset": "bot_pos_skew_min_reward_offset_cents",
    "max_completable_pair_cost": "bot_pos_price_max_complete_usd",
    "enforce_completable_pair_cost": "bot_pos_gate_complete_cost_on",
    "merge_max_loss_per_share": "bot_pos_merge_max_loss_per_share_usd",
    "merge_velocity_hold_days": "bot_pos_merge_velocity_hold_days",
    "strict_paired_inventory": "bot_pos_gate_strict_pairs_on",
    "select_spread_pct": "filter_std_spread_share_pct",
    # Order
    "max_fills_per_market": "bot_order_max_fills_per_market",
    "requote_interval_sec": "bot_order_requote_interval_sec",
    "requote_dead_band": "bot_order_requote_dead_band_cents",
    "requote_hold_queue_shares": "bot_order_requote_hold_queue_shares",
    "requote_hold_below_target": "bot_order_requote_hold_below_target_cents",
    "poll_interval_sec": "bot_order_poll_interval_sec",
    "enforce_queue_clear_gate": "bot_order_gate_queue_clear_on",
    "max_queue_clear_minutes": "bot_order_queue_max_clear_min",
    "queue_flow_window_sec": "bot_order_queue_flow_window_sec",
    "min_t_remaining_sec": "bot_order_min_t_remaining_sec",
    # Risk
    "max_cost_per_market": "bot_risk_exposure_max_cost_per_market_usd",
    "max_open_markets": "bot_risk_exposure_max_open_markets",
    "scarcity_marginal_multiple": "bot_risk_scarcity_marginal_multiple",
    "capital_scarce": "bot_risk_capital_scarce",
    "max_fleet_naked_usd": "bot_risk_exposure_max_fleet_naked_usd",
    "fleet_naked_usd": "bot_risk_exposure_fleet_naked_usd",
    "max_committed_usd": "bot_risk_exposure_max_committed_usd",
    "committed_usd": "bot_risk_exposure_committed_usd",
    "fleet_posture": "bot_risk_circuit_fleet_posture",
    "enable_pairs_rule": "bot_risk_gate_pairs_rule_on",
    "pairs_exit_window_sec": "bot_risk_pairs_exit_window_sec",
    "enable_aged_out_rescue": "bot_risk_gate_aged_out_rescue_on",
    "aged_out_rescue_lead_sec": "bot_risk_aged_out_rescue_lead_sec",
    "pairs_complete_gain_cents": "bot_risk_pairs_complete_gain_cents",
    "pairs_exit_cost_cents": "bot_risk_pairs_exit_cost_cents",
    "stop_loss_mode": "bot_risk_stop_mode",
    "drop_cents": "bot_risk_stop_adverse_drop_cents",
    "drawdown_pct": "bot_risk_stop_max_drawdown_pct",
    "emergency_drawdown_pct": "bot_risk_circuit_max_drawdown_pct",
    "emergency_hedge_pct": "bot_risk_hedge_emergency_pct",
    "enable_emergency_hedge": "bot_risk_gate_emergency_hedge_on",
    "enable_hard_blocks": "bot_risk_gate_hard_blocks_on",
    "decided_price": "bot_risk_circuit_decided_price_cents",
    "widen_offset": "bot_risk_circuit_widen_offset_cents",
    "gate_state": "bot_risk_circuit_gate_state",
    "markout_horizons": "bot_risk_markout_horizons",
    "markout_min_sample": "bot_risk_markout_min_sample",
    "markout_fleet_min_sample": "bot_risk_markout_fleet_min_sample",
    "markout_widen_threshold": "bot_risk_markout_widen_threshold",
    "markout_catastrophic_threshold": "bot_risk_markout_catastrophic_threshold",
    "stranded_completion_enabled": "bot_risk_stranded_completion_on",
    "stranded_max_wait_sec": "bot_risk_stranded_max_wait_sec",
    # Eval / Latency / Market
    "stat_gate_deltas": "bot_eval_stat_gate_deltas",
    "stat_gate_threshold_pct": "bot_eval_stat_gate_threshold_pct",
    "stat_gate_bankroll_pct": "bot_eval_stat_gate_bankroll_pct",
    "stat_gate_target_closes": "bot_eval_stat_gate_target_closes",
    "maker_fee": "bot_econ_maker_fee",
    "fee_rate": "bot_econ_fee_rate",
    "net_oneway_ms": "bot_latency_net_oneway_ms",
    "cancel_venue_ack_ms": "bot_latency_cancel_venue_ack_ms",
    "post_venue_accept_ms": "bot_latency_post_venue_accept_ms",
    "pinned_condition_id": "bot_market_pinned_condition_id",
    "market_title": "bot_market_title",
    "market_url": "bot_market_url",
    "series_slug": "bot_market_series_slug",
}

_PROFILE_CANONICAL_TO_LEGACY: dict[str, str] = {
    "bot_quote_bank_baseline_usd": "bankroll_usd",
    "bot_risk_exposure_max_naked_usd": "max_naked_usd",
    "bot_quote_size_min_shares": "min_quote_shares",
    "bot_quote_book_max_spread_from_mid": "max_spread_from_mid",
    "bot_pos_price_max_pair_cost_usd": "max_pair_cost",
}
_PROFILE_FIELDS = set(_PROFILE_CANONICAL_TO_LEGACY.values())


class _LegacyProperty:
    def __init__(self, target_name: str, legacy_name: str, default_val: object):
        self.target_name = target_name
        self.legacy_name = legacy_name
        self.default_val = default_val

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self.default_val
        warnings.warn(f"{self.legacy_name} is deprecated; use {self.target_name} instead", DeprecationWarning, stacklevel=2)
        return getattr(obj, self.target_name)

    def __set__(self, obj, val):
        warnings.warn(f"{self.legacy_name} is deprecated; use {self.target_name} instead", DeprecationWarning, stacklevel=2)
        object.__setattr__(obj, self.target_name, val)


class _ConfigBase:
    """Base class providing seamless backwards compatibility and argument translation."""
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        old_init = getattr(cls, "__init__", None)
        def _wrapped_init(self, *args, **kw):
            _READ_ONLY_PROPERTIES = {
                "emergency_hedge_frac", "max_skew", "select_spread_frac",
                "cancel_net_oneway_ms", "min_book_depth_sh", "couple_risk_frac",
                "quote_window_frac", "stat_gate_bankroll_fraction",
                "select_max_book_spread", "filter_std_spread_max_book",
                "tau_cancel_sec", "tau_post_sec",
            }
            for ro_prop in _READ_ONLY_PROPERTIES:
                if ro_prop in kw:
                    raise TypeError(f"MakerConfig.__init__() got an unexpected keyword argument {ro_prop!r}")
            if args:
                field_list = [
                    f for f in fields(cls)
                    if f.name not in _LEGACY_TO_CANONICAL
                    and not (isinstance(getattr_static(cls, f.name, None), property) or type(getattr_static(cls, f.name, None)).__name__ == "_LegacyProperty")
                ]
                for f, val in zip(field_list, args):
                    kw[f.name] = val
            # Map canonical profile args to legacy fields
            for can, leg in _PROFILE_CANONICAL_TO_LEGACY.items():
                if can in kw:
                    can_val = kw.pop(can)
                    if leg not in kw:
                        kw[leg] = can_val
            # Map legacy args to canonical fields
            for leg, can in _LEGACY_TO_CANONICAL.items():
                if leg in kw:
                    leg_val = kw.pop(leg)
                    if can in kw:
                        default_val = getattr(cls, can)
                        if leg_val != default_val and kw[can] == default_val:
                            kw[can] = leg_val
                    else:
                        kw[can] = leg_val
            # Fill default values for any fields not supplied
            for f in fields(cls):
                if f.name in _LEGACY_TO_CANONICAL:
                    continue
                if f.name not in kw and hasattr(cls, f.name):
                    raw_val = getattr_static(cls, f.name, None)
                    if not (isinstance(raw_val, property) or type(raw_val).__name__ == "_LegacyProperty"):
                        kw[f.name] = getattr(cls, f.name)
            for k, v in kw.items():
                object.__setattr__(self, k, v)
        cls.__init__ = _wrapped_init


@dataclass(frozen=True)
class MakerConfig(_ConfigBase):
    # =========================================================================
    # 5 PROFILE FIELDS (declared directly on core so PICKER_PROFILE matches)
    # =========================================================================
    bankroll_usd: float = 100.0
    max_naked_usd: float = 6.0
    min_quote_shares: int = 5
    max_spread_from_mid: float = 0.15
    max_pair_cost: float = 0.99
    max_book_spread: float = 0.06

    # =========================================================================
    # SCOPE 1: MARKET FILTERING (MARKET SCREENER)
    # =========================================================================
    # 1. Active / In-Play Markets (Live Sports & Esports)
    filter_live_vol_min_24h_usd: float = 35_000.0
    filter_live_vel_lookback_sec: float = 1800.0        # 30m lookback window
    filter_live_vel_min_usd: float = 200.0
    filter_live_move_lookback_sec: float = 7200.0       # 2h lookback window
    filter_live_move_min_range_cents: float = 2.0
    filter_live_gate_velocity_on: bool = True
    filter_live_activity_min_ratio: float = 0.0

    # 2. Standard / Pre-Match & Long-Dated Markets
    filter_std_vol_min_24h_usd: float = 125_000.0
    filter_std_vol_min_24h_usd_trial: float | None = None
    filter_common_vol_min_top3_depth_usd: float = 500.0
    filter_common_vol_min_top3_depth_usd_trial: float | None = None
    filter_std_spread_share_pct: float = 41.0 / 120.0
    filter_std_time_max_days_to_resolve: float = 30.0
    filter_std_rank_sample_window_sec: float = 1800.0
    filter_std_rerank_interval_sec: float = 600.0
    filter_std_queue_max_clear_min: float = 15.0
    filter_std_gate_queue_max_on: bool = False

    # =========================================================================
    # SCOPE 2: BOT ENGINE (SHARED EQUALLY BY LIVE TRADING & SHADOW PAPER MODE)
    # =========================================================================
    # 1. Quoting Phase (Entry & Book Health)
    bot_quote_both_sides_on: bool = True
    bot_quote_ticks_below_ask: int = 1
    bot_quote_tick_size: float = 0.01
    bot_quote_grid_tick_size_usd: float = 0.01
    bot_quote_gate_widen_on: bool = False
    bot_quote_size_mode: str = "dollars"
    bot_quote_size_shares: int = 120
    bot_quote_size_max_shares: float | None = None
    bot_quote_alloc_couple_pct: float = 0.01
    bot_quote_alloc_couple_min_usd: float = 10.0
    bot_quote_waive_attenuation_below_floor_on: bool = True
    bot_quote_require_two_sided_when_flat_on: bool = True
    bot_quote_price_band_min_cents: float = 0.10
    bot_quote_price_band_max_cents: float = 0.90
    bot_quote_window_max_pct: float = 0.40
    bot_quote_gate_price_band_on: bool = True
    bot_quote_gate_quote_window_on: bool = True
    bot_quote_coinflip_halfwidth: float = 0.20
    bot_quote_coinflip_size_cut: float = 0.55
    bot_quote_price_risk_widen: float = 0.010
    bot_quote_book_min_depth_shares: float = 200.0
    bot_quote_wide_book_trial: float | None = None
    bot_quote_spread_static_offset_cents: float = 0.020
    bot_quote_gate_dynamic_offset_on: bool = True
    bot_quote_spread_offset_multiplier: float = 0.50
    bot_quote_spread_offset_min_cents: int = 1
    bot_quote_spread_offset_max_cents: int = 20
    bot_quote_spread_offset_max_age_sec: float = 900.0
    bot_quote_telemetry_range_cents: float | None = None
    bot_quote_telemetry_velocity_measured_at: float | None = None
    bot_risk_exposure_max_naked_pct: float = 0.06
    bot_risk_exposure_max_order_pct: float = 0.25
    bot_risk_exposure_bankroll_ceiling_pct: float = 0.90
    bot_risk_exposure_max_order_usd: float = 25.0
    bot_risk_exposure_max_total_usd: float = 90.0

    # 2. Open Positions Management Phase (Inventory & Merge)
    bot_pos_objective: str = "spread_capture"
    bot_pos_gate_strict_pairs_on: bool = True
    bot_pos_skew_target_balance_ratio: float = 0.92
    bot_pos_skew_max_offset_cents: float = 0.015
    bot_pos_skew_min_reward_offset_cents: float = 0.005
    bot_pos_price_max_complete_usd: float = 1.00
    bot_pos_gate_complete_cost_on: bool = True
    bot_pos_merge_max_loss_per_share_usd: float = 0.01
    bot_pos_merge_velocity_hold_days: float = 30.0

    # 3. Protection, Stops, Requotes & Order Lifecycle Phase
    bot_order_max_fills_per_market: int = 25
    bot_order_requote_interval_sec: float = 2.0
    bot_order_requote_dead_band_cents: float = 0.03
    bot_order_requote_hold_queue_shares: float = 0.0
    bot_order_requote_hold_below_target_cents: float = 0.05
    bot_order_poll_interval_sec: float = 1.0
    bot_order_gate_queue_clear_on: bool = False
    bot_order_queue_max_clear_min: float = 60.0
    bot_order_queue_flow_window_sec: float = 1800.0
    bot_order_min_t_remaining_sec: float = 15.0

    bot_risk_exposure_max_cost_per_market_usd: float = 400.0
    bot_risk_exposure_max_open_markets: int = 3
    bot_risk_scarcity_marginal_multiple: float = 2.0
    bot_risk_capital_scarce: bool = False
    bot_risk_exposure_max_fleet_naked_usd: float = 400.0
    bot_risk_exposure_fleet_naked_usd: float = 0.0
    bot_risk_exposure_max_committed_usd: float = 1000.0
    bot_risk_exposure_committed_usd: float = 0.0
    bot_risk_circuit_fleet_posture: str = "NORMAL"
    bot_risk_gate_pairs_rule_on: bool = True
    bot_risk_pairs_exit_window_sec: float = 900.0
    bot_risk_gate_aged_out_rescue_on: bool = True
    bot_risk_aged_out_rescue_lead_sec: float = 900.0
    bot_risk_pairs_complete_gain_cents: float = 3.68
    bot_risk_pairs_exit_cost_cents: float = 3.67
    bot_risk_stop_mode: str = "NONE"
    bot_risk_stop_adverse_drop_cents: float = 0.05
    bot_risk_stop_max_drawdown_pct: float = 10.0
    bot_risk_circuit_max_drawdown_pct: float = 75.0
    bot_risk_hedge_emergency_pct: float = 0.8
    bot_risk_gate_emergency_hedge_on: bool = True
    bot_risk_gate_hard_blocks_on: bool = True
    bot_risk_circuit_decided_price_cents: float = 0.02
    bot_risk_circuit_widen_offset_cents: float = 0.035
    bot_risk_circuit_gate_state: str = "NORMAL"
    bot_risk_markout_horizons: tuple[float, ...] = (300.0, 3600.0, 21600.0, 900.0)
    bot_risk_markout_min_sample: int = 8
    bot_risk_markout_fleet_min_sample: int = 25
    bot_risk_markout_widen_threshold: float = -0.005
    bot_risk_markout_catastrophic_threshold: float = -0.020
    bot_risk_stranded_completion_on: bool = False
    bot_risk_stranded_max_wait_sec: float = 300.0

    # 4. Evaluation, Latency, Markets
    bot_eval_stat_gate_deltas: tuple[float, ...] = (0.01, 0.02, 0.04)
    bot_eval_stat_gate_threshold_pct: float = 1.0
    bot_eval_stat_gate_bankroll_pct: float = 0.01
    bot_eval_stat_gate_target_closes: int = 60
    bot_econ_maker_fee: float = 0.0
    bot_econ_fee_rate: float = 0.07
    bot_latency_net_oneway_ms: float = 3.93
    bot_latency_cancel_venue_ack_ms: float = 150.0
    bot_latency_post_venue_accept_ms: float = 81.0
    bot_market_pinned_condition_id: str = ""
    bot_market_title: str = ""
    bot_market_url: str = ""
    bot_market_series_slug: str = "btc-up-or-down-5m"

    # =========================================================================
    # CANONICAL ALIASES FOR PROFILE FIELDS
    # =========================================================================
    @property
    def bot_quote_bank_baseline_usd(self) -> float:
        return self.bankroll_usd

    @bot_quote_bank_baseline_usd.setter
    def bot_quote_bank_baseline_usd(self, val: float) -> None:
        object.__setattr__(self, "bankroll_usd", val)

    @property
    def bot_risk_exposure_max_naked_usd(self) -> float:
        return self.max_naked_usd

    @bot_risk_exposure_max_naked_usd.setter
    def bot_risk_exposure_max_naked_usd(self, val: float) -> None:
        object.__setattr__(self, "max_naked_usd", val)

    @property
    def bot_quote_size_min_shares(self) -> int:
        return self.min_quote_shares

    @bot_quote_size_min_shares.setter
    def bot_quote_size_min_shares(self, val: int) -> None:
        object.__setattr__(self, "min_quote_shares", val)

    @property
    def bot_quote_book_max_spread_from_mid(self) -> float:
        return self.max_spread_from_mid

    @bot_quote_book_max_spread_from_mid.setter
    def bot_quote_book_max_spread_from_mid(self, val: float) -> None:
        object.__setattr__(self, "max_spread_from_mid", val)

    @property
    def bot_pos_price_max_pair_cost_usd(self) -> float:
        return self.max_pair_cost

    @bot_pos_price_max_pair_cost_usd.setter
    def bot_pos_price_max_pair_cost_usd(self, val: float) -> None:
        object.__setattr__(self, "max_pair_cost", val)

    # =========================================================================
    # DERIVED & LEGACY PROPERTIES
    # =========================================================================
    @property
    def select_max_book_spread(self) -> float:
        """The picking bar: the live gate times the picking share (#572)."""
        if self.wide_book_trial is not None:
            return self.wide_book_trial
        return round(self.max_book_spread * self.select_spread_pct, 6)

    @property
    def cancel_net_oneway_ms(self) -> float:
        """Backward compatibility alias for net_oneway_ms."""
        return self.net_oneway_ms

    @property
    def max_skew(self) -> float:
        """Backward compatibility alias for max_skew_offset (#632)."""
        return self.max_skew_offset

    @property
    def min_book_depth_sh(self) -> float:
        """Backward compatibility alias for min_book_depth_shares (#632)."""
        return self.min_book_depth_shares

    @property
    def emergency_hedge_frac(self) -> float:
        """Backward compatibility alias for emergency_hedge_pct (#632)."""
        return self.emergency_hedge_pct

    @property
    def couple_risk_frac(self) -> float:
        """Backward compatibility alias for couple_risk_pct (#632)."""
        return self.couple_risk_pct

    @property
    def quote_window_frac(self) -> float:
        """Backward compatibility alias for quote_window_pct (#632)."""
        return self.quote_window_pct

    @property
    def stat_gate_bankroll_fraction(self) -> float:
        """Backward compatibility alias for stat_gate_bankroll_pct (#632)."""
        return self.stat_gate_bankroll_pct

    @property
    def select_spread_frac(self) -> float:
        """Backward compatibility alias for select_spread_pct (#632, #572)."""
        return self.select_spread_pct

    @property
    def tau_cancel_sec(self) -> float:
        return (self.net_oneway_ms + self.cancel_venue_ack_ms) / 1000.0

    @property
    def tau_post_sec(self) -> float:
        return (self.net_oneway_ms + self.post_venue_accept_ms) / 1000.0

    def db_path(self) -> Path:
        return Path(os.environ.get("SPREAD_HUNTER_DB") or os.environ.get("HUNTER_DB", str(ROOT / "hunter.db")))

    @classmethod
    def from_env(cls, *, for_display: bool = False) -> MakerConfig:
        """Config loader checking canonical uppercase env var first, falling back to HUNTER_*."""
        kw: dict = {}
        preset_raw, _ = _env_get("BOT_QUOTE_TOURNAMENT_PRESET", "HUNTER_TOURNAMENT_PRESET")
        preset_name = (preset_raw or "").strip().lower()
        if preset_name:
            if preset_name not in TOURNAMENT_PRESETS:
                raise ValueError(
                    f"HUNTER_TOURNAMENT_PRESET={preset_name!r} is unknown. "
                    f"Valid presets: {list(TOURNAMENT_PRESETS.keys())}"
                )
            kw.update({k: v for k, v in TOURNAMENT_PRESETS[preset_name].items() if k != "description"})

        cid, _ = _env_get("BOT_MARKET_PINNED_CONDITION_ID", "HUNTER_MARKET")
        if cid and cid.strip():
            kw["pinned_condition_id"] = cid.strip()
            title, _ = _env_get("BOT_MARKET_TITLE", "HUNTER_TITLE")
            kw["market_title"] = title or ""
            url, _ = _env_get("BOT_MARKET_URL", "HUNTER_URL")
            kw["market_url"] = url or ""
            kw["min_t_remaining_sec"] = 0.0

            ms, _ = _env_get("BOT_QUOTE_SIZE_MIN_SHARES", "HUNTER_MIN_QUOTE_SHARES")
            if ms is None:
                ms = os.environ.get("HUNTER_MIN_SIZE")
                if ms is not None and ms.strip():
                    logging.getLogger(__name__).warning(
                        "HUNTER_MIN_SIZE is deprecated (#632); set HUNTER_MIN_QUOTE_SHARES instead."
                    )
            if ms and ms.strip():
                kw["min_quote_shares"] = int(float(ms))
                kw["quote_shares"] = max(int(float(ms)), 120)

            sp, _ = _env_get("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", "HUNTER_MAX_SPREAD_CENTS")
            if sp is None:
                sp = os.environ.get("HUNTER_MAX_SPREAD")
                if sp is not None and sp.strip():
                    logging.getLogger(__name__).warning(
                        "HUNTER_MAX_SPREAD is deprecated (#632); set HUNTER_MAX_SPREAD_CENTS instead."
                    )
            if sp and sp.strip():
                kw["max_spread_from_mid"] = float(sp) / 100.0

            tk, _ = _env_get("BOT_QUOTE_GRID_TICK_SIZE_USD", "HUNTER_PRICE_TICK")
            if tk is None:
                tk = os.environ.get("HUNTER_TICK")
                if tk is not None and tk.strip():
                    logging.getLogger(__name__).warning(
                        "HUNTER_TICK is deprecated (#632); set HUNTER_PRICE_TICK instead."
                    )
            if tk and tk.strip():
                kw["price_tick"] = float(tk)

        raw_sp_general, _ = _env_get("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", "HUNTER_MAX_SPREAD_CENTS")
        if raw_sp_general is None:
            raw_sp_general = os.environ.get("HUNTER_MAX_SPREAD")
            if raw_sp_general is not None and raw_sp_general.strip():
                logging.getLogger(__name__).warning(
                    "HUNTER_MAX_SPREAD is deprecated (#632); set HUNTER_MAX_SPREAD_CENTS instead."
                )
        sp_general = raw_sp_general or ""
        if sp_general.strip():
            try:
                cents = float(sp_general)
            except ValueError:
                cents = 0.0
            if cents > 0:
                kw["max_spread_from_mid"] = cents / 100.0

        awg, _ = _env_get("BOT_QUOTE_GATE_WIDEN_ON", "HUNTER_APPLY_WIDEN_GATE")
        if awg and awg.strip():
            kw["apply_widen_gate"] = awg.strip().lower() not in ("0", "false", "off", "no")

        wide_raw, wide_name = _env_get("BOT_QUOTE_WIDE_BOOK_TRIAL", "HUNTER_WIDE_BOOK_TRIAL")
        wide = resolve_wide_book_trial(wide_raw or "", for_display=for_display, var_name=wide_name or "HUNTER_WIDE_BOOK_TRIAL")
        if wide is not None and wide > 0:
            kw["wide_book_trial"] = wide
            kw["max_book_spread"] = wide
            window = kw.get("max_spread_from_mid", cls.max_spread_from_mid)
            kw["max_spread_from_mid"] = max(window, wide)

        trial, _ = _env_get("FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL", "HUNTER_DEPTH_TRIAL_USD")
        if trial and trial.strip():
            kw["select_min_top3_depth_usd_trial"] = float(trial)

        vtri, _ = _env_get("FILTER_STD_VOL_MIN_24H_USD_TRIAL", "HUNTER_VOLUME_TRIAL_USD")
        if vtri and vtri.strip():
            kw["select_min_volume_24h_usd_trial"] = float(vtri)

        pr, _ = _env_get("BOT_RISK_GATE_PAIRS_RULE_ON", "HUNTER_PAIRS_RULE")
        if pr and pr.strip():
            kw["enable_pairs_rule"] = pr.strip().lower() not in ("0", "false", "off")

        pcap, pcap_name = _env_get("BOT_POS_PRICE_MAX_PAIR_COST_USD", "HUNTER_PAIR_COST_CAP")
        if pcap and pcap.strip():
            kw["max_pair_cost"] = resolve_pair_cost_trial(pcap, for_display=for_display, var_name=pcap_name or "HUNTER_PAIR_COST_CAP")

        ccap, ccap_name = _env_get("BOT_POS_PRICE_MAX_COMPLETE_USD", "HUNTER_COMPLETABLE_CAP")
        if ccap and ccap.strip():
            kw["max_completable_pair_cost"] = _bounded_float(
                ccap_name or "HUNTER_COMPLETABLE_CAP", ccap, 0.0, 1.0)

        rdb, rdb_name = _env_get("BOT_ORDER_REQUOTE_DEAD_BAND_CENTS", "HUNTER_REQUOTE_DEAD_BAND")
        if rdb and rdb.strip():
            kw["requote_dead_band"] = _bounded_float(
                rdb_name or "HUNTER_REQUOTE_DEAD_BAND", rdb, 0.0, 1.0)

        rhb, rhb_name = _env_get("BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS", "HUNTER_REQUOTE_HOLD_BELOW")
        if rhb and rhb.strip():
            kw["requote_hold_below_target"] = _bounded_float(
                rhb_name or "HUNTER_REQUOTE_HOLD_BELOW", rhb, 0.0, 1.0)

        rhq, rhq_name = _env_get("BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES", "HUNTER_REQUOTE_HOLD_QUEUE")
        if rhq and rhq.strip():
            kw["requote_hold_queue_shares"] = _bounded_float(
                rhq_name or "HUNTER_REQUOTE_HOLD_QUEUE", rhq, 0.0, 2_000_000.0)

        qcg, _ = _env_get("BOT_ORDER_GATE_QUEUE_CLEAR_ON", "HUNTER_QUEUE_CLEAR_GATE")
        if qcg and qcg.strip():
            kw["enforce_queue_clear_gate"] = qcg.strip().lower() not in ("0", "false", "off")

        qcm, qcm_name = _env_get("BOT_ORDER_QUEUE_MAX_CLEAR_MIN", "HUNTER_MAX_QUEUE_CLEAR_MIN")
        if qcm and qcm.strip():
            kw["max_queue_clear_minutes"] = _bounded_float(
                qcm_name or "HUNTER_MAX_QUEUE_CLEAR_MIN", qcm, 0.1, 1440.0)

        prw, prw_name = _env_get("BOT_QUOTE_PRICE_RISK_WIDEN", "HUNTER_PRICE_RISK_WIDEN")
        if prw and prw.strip():
            kw["price_risk_widen"] = _bounded_float(
                prw_name or "HUNTER_PRICE_RISK_WIDEN", prw, 0.0, 1.0)

        cno, _ = _env_get("BOT_LATENCY_NET_ONEWAY_MS", "HUNTER_NET_ONEWAY_MS", "HUNTER_CANCEL_NET_ONEWAY_MS")
        if cno and cno.strip():
            kw["net_oneway_ms"] = float(cno)

        cva, _ = _env_get("BOT_LATENCY_CANCEL_VENUE_ACK_MS", "HUNTER_CANCEL_VENUE_ACK_MS")
        if cva and cva.strip():
            kw["cancel_venue_ack_ms"] = float(cva)

        pva, _ = _env_get("BOT_LATENCY_POST_VENUE_ACCEPT_MS", "HUNTER_POST_VENUE_ACCEPT_MS")
        if pva and pva.strip():
            kw["post_venue_accept_ms"] = float(pva)

        bk, _ = _env_get("BOT_QUOTE_BANK_BASELINE_USD", "SPREAD_HUNTER_BANKROLL", "HUNTER_BANKROLL")
        if bk and bk.strip():
            val = float(bk)
            if not math.isfinite(val) or val <= 0:
                raise ValueError(f"Bankroll override must be finite and strictly positive, got: {val}")
            kw["bankroll_usd"] = val
            kw["max_committed_usd"] = val

        slm, slm_name = _env_get("BOT_RISK_STOP_MODE", "HUNTER_STOP_LOSS_MODE")
        if slm and slm.strip():
            mode = slm.strip().upper()
            if mode not in STOP_LOSS_MODES:
                choices = ", ".join(sorted(STOP_LOSS_MODES))
                var_label = slm_name or "HUNTER_STOP_LOSS_MODE"
                raise ValueError(
                    f"{var_label} must be one of {choices}, got: {slm}"
                )
            kw["stop_loss_mode"] = mode

        dc, dc_name = _env_get("BOT_RISK_STOP_ADVERSE_DROP_CENTS", "HUNTER_DROP_CENTS")
        if dc and dc.strip():
            kw["drop_cents"] = _bounded_float(
                dc_name or "HUNTER_DROP_CENTS", dc, DROP_CENTS_MIN, DROP_CENTS_MAX)

        for _can_name, _leg_name, _field in (
            ("BOT_RISK_STOP_MAX_DRAWDOWN_PCT", "HUNTER_DRAWDOWN_PCT", "drawdown_pct"),
            ("BOT_RISK_CIRCUIT_MAX_DRAWDOWN_PCT", "HUNTER_EMERGENCY_DRAWDOWN_PCT", "emergency_drawdown_pct"),
        ):
            _raw, _vname = _env_get(_can_name, _leg_name)
            if _raw and _raw.strip():
                _val = _bounded_float(_vname or _leg_name, _raw, 0.0, 100.0)
                if _val <= 0.0:
                    raise ValueError(
                        f"{_vname or _leg_name}={_raw!r} must be greater than zero"
                    )
                kw[_field] = _val

        for _old_env, _replacement in (
            ("HUNTER_SINGLE_BUY_GRACE_SEC", "HUNTER_AGED_OUT_RESCUE_LEAD_SEC (patience)"),
            ("HUNTER_STOP_LOSS_TRIGGER_USD_CENTS", "HUNTER_DROP_CENTS (dollars) or HUNTER_DRAWDOWN_PCT"),
            ("HUNTER_STOP_LOSS_TRIGGER_PCT", "HUNTER_DRAWDOWN_PCT"),
        ):
            if os.environ.get(_old_env):
                logging.getLogger(__name__).warning(
                    "%s is retired and ignored (#619); set %s instead.",
                    _old_env, _replacement)

        for _old_env in ("HUNTER_REFUSAL_HOLD_POLICY", "HUNTER_REFUSAL_HOLD_DRIFT"):
            if os.environ.get(_old_env):
                logging.getLogger(__name__).warning(
                    "%s is retired and ignored (#622); it has no replacement.",
                    _old_env)

        for _old_env in ("HUNTER_SINGLE_BUY_MAX_LOSS_PCT", "HUNTER_SINGLE_BUY_MAX_LOSS_USD"):
            if os.environ.get(_old_env):
                logging.getLogger(__name__).warning(
                    "%s is deprecated and ignored (#573 renamed it); #619 replaced "
                    "both with the entry-relative stop: set HUNTER_STOP_LOSS_MODE "
                    "plus HUNTER_DROP_CENTS / HUNTER_DRAWDOWN_PCT instead.", _old_env)

        aorl, _ = _env_get("BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC", "HUNTER_AGED_OUT_RESCUE_LEAD_SEC")
        if aorl and aorl.strip():
            val = float(aorl)
            if not math.isfinite(val) or val < 0:
                raise ValueError(
                    "HUNTER_AGED_OUT_RESCUE_LEAD_SEC must be finite and "
                    f"non-negative, got: {val}")
            kw["aged_out_rescue_lead_sec"] = val

        mqs, _ = _env_get("BOT_QUOTE_SIZE_MAX_SHARES", "HUNTER_MAX_QUOTE_SHARES")
        if mqs is None:
            mqs = os.environ.get("HUNTER_MAX_SHARES") or os.environ.get("SPREAD_HUNTER_MAX_SHARES")
            if mqs is not None and mqs.strip():
                logging.getLogger(__name__).warning(
                    "HUNTER_MAX_SHARES is deprecated (#632); set HUNTER_MAX_QUOTE_SHARES instead."
                )
        if mqs and mqs.strip():
            val = float(mqs)
            if not math.isfinite(val) or val <= 0:
                raise ValueError(f"HUNTER_MAX_QUOTE_SHARES must be strictly positive, got: {val}")
            kw["max_quote_shares"] = val

        sgt, sgt_name = _env_get("BOT_EVAL_STAT_GATE_THRESHOLD_PCT", "HUNTER_STAT_GATE_THRESHOLD_PCT")
        if sgt and sgt.strip():
            kw["stat_gate_threshold_pct"] = _bounded_float(
                sgt_name or "HUNTER_STAT_GATE_THRESHOLD_PCT", sgt, -100.0, 100.0)

        sgb, sgb_name = _env_get("BOT_EVAL_STAT_GATE_BANKROLL_PCT", "HUNTER_STAT_GATE_BANKROLL_PCT")
        if sgb is None:
            sgb = os.environ.get("HUNTER_STAT_GATE_BANKROLL_FRACTION")
            if sgb is not None and sgb.strip():
                logging.getLogger(__name__).warning(
                    "HUNTER_STAT_GATE_BANKROLL_FRACTION is deprecated (#632); set HUNTER_STAT_GATE_BANKROLL_PCT instead."
                )
                sgb_name = "HUNTER_STAT_GATE_BANKROLL_FRACTION"
        if sgb and sgb.strip():
            kw["stat_gate_bankroll_pct"] = _bounded_float(
                sgb_name or "HUNTER_STAT_GATE_BANKROLL_PCT", sgb, 0.0, 1.0)

        stc, stc_name = _env_get("BOT_EVAL_STAT_GATE_TARGET_CLOSES", "HUNTER_STAT_GATE_TARGET_CLOSES")
        if stc and stc.strip():
            try:
                val = int(stc)
                if val <= 0:
                    raise ValueError
            except ValueError as exc:
                var_label = stc_name or "HUNTER_STAT_GATE_TARGET_CLOSES"
                raise ValueError(
                    f"{var_label} must be a positive integer, got: {stc!r}"
                ) from exc
            kw["stat_gate_target_closes"] = val

        dyn, _ = _env_get("BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON", "HUNTER_DYNAMIC_OFFSET")
        if dyn and dyn.strip():
            kw["dynamic_offset_enabled"] = dyn.strip().lower() in ("1", "true", "yes", "on")

        dyn_mult, dyn_mult_name = _env_get("BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER", "HUNTER_DYNAMIC_OFFSET_MULT")
        if dyn_mult and dyn_mult.strip():
            kw["dynamic_offset_multiplier"] = _bounded_float(
                dyn_mult_name or "HUNTER_DYNAMIC_OFFSET_MULT", dyn_mult, 0.0, 5.0)

        dyn_min, dyn_min_name = _env_get("BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS", "HUNTER_DYNAMIC_OFFSET_MIN_CENTS")
        if dyn_min and dyn_min.strip():
            kw["dynamic_offset_min_cents"] = int(_bounded_float(
                dyn_min_name or "HUNTER_DYNAMIC_OFFSET_MIN_CENTS", dyn_min, 1.0, 10.0))

        dyn_max, dyn_max_name = _env_get("BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS", "HUNTER_DYNAMIC_OFFSET_MAX_CENTS")
        if dyn_max and dyn_max.strip():
            kw["dynamic_offset_max_cents"] = int(_bounded_float(
                dyn_max_name or "HUNTER_DYNAMIC_OFFSET_MAX_CENTS", dyn_max, 1.0, 20.0))

        dyn_age, dyn_age_name = _env_get("BOT_QUOTE_SPREAD_OFFSET_MAX_AGE_SEC", "HUNTER_DYNAMIC_OFFSET_MAX_AGE")
        if dyn_age and dyn_age.strip():
            kw["dynamic_offset_max_age_sec"] = _bounded_float(
                dyn_age_name or "HUNTER_DYNAMIC_OFFSET_MAX_AGE", dyn_age, 0.0, 86400.0)

        return cls(**kw)


# Wire properties and register legacy fields in MakerConfig.__dataclass_fields__
for _leg, _can in _LEGACY_TO_CANONICAL.items():
    _def = getattr(MakerConfig, _can)
    setattr(MakerConfig, _leg, _LegacyProperty(_can, _leg, _def))
    _field_obj = Field(_def, MISSING, True, True, None, True, {}, False)
    _field_obj.name = _leg
    _field_obj.type = type(_def)
    _field_obj._field_type = _FIELD
    MakerConfig.__dataclass_fields__[_leg] = _field_obj


# THE PICKER PROFILE (#571). The five settings the ranker deliberately runs at
# a different value from the live fleet, in one table so both sides of every
# disagreement can be read in one place. `scoring.config.MakerConfig` reads
# these as its own defaults and declares no literal of its own; the live values
# on the class above are untouched by them.
PICKER_PROFILE: dict[str, float | int] = {
    "bankroll_usd": 1000.0,
    "max_naked_usd": 120.0,
    "min_quote_shares": 50,
    "max_spread_from_mid": 0.045,
    "max_pair_cost": 0.995,
}


TOURNAMENT_PRESETS: dict[str, dict] = {
    "conservative": {
        "dynamic_offset_enabled": True,
        "dynamic_offset_multiplier": 0.75,
        "dynamic_offset_min_cents": 2,
        "dynamic_offset_max_cents": 5,
        "requote_hold_queue_shares": 500.0,
        "requote_hold_below_target": 0.08,
        "description": "Wider safety buffer in volatile swings (2¢–5¢) with queue hold",
    },
    "balanced": {
        "dynamic_offset_enabled": True,
        "dynamic_offset_multiplier": 0.50,
        "dynamic_offset_min_cents": 2,
        "dynamic_offset_max_cents": 4,
        "requote_hold_queue_shares": 500.0,
        "requote_hold_below_target": 0.08,
        "description": "Proportional volatility tracking (2¢–4¢) with queue hold",
    },
    "aggressive": {
        "dynamic_offset_enabled": True,
        "dynamic_offset_multiplier": 0.25,
        "dynamic_offset_min_cents": 2,
        "dynamic_offset_max_cents": 3,
        "requote_hold_queue_shares": 500.0,
        "requote_hold_below_target": 0.08,
        "description": "Tight queue priority capturing fast fills (2¢–3¢) with queue hold",
    },
    "prudent": {
        "dynamic_offset_enabled": True,
        "dynamic_offset_multiplier": 0.60,
        "dynamic_offset_min_cents": 2,
        "dynamic_offset_max_cents": 4,
        "requote_hold_queue_shares": 500.0,
        "requote_hold_below_target": 0.08,
        "description": "Hybrid conservative-balanced tracking (2¢–4¢, 0.60x) with queue hold",
    },
    "hybrid": {
        "dynamic_offset_enabled": False,
        "requote_dead_band": 0.060,
        "requote_hold_queue_shares": 500.0,
        "requote_hold_below_target": 0.08,
        "description": "Empirical Winner Hybrid: control edge + Deadband-Wide queue stability (6c deadband)",
    },
    "baseline": {
        "dynamic_offset_enabled": True,
        "dynamic_offset_multiplier": 0.50,
        "dynamic_offset_min_cents": 1,
        "dynamic_offset_max_cents": 20,
        "max_spread_from_mid": 0.15,
        "apply_widen_gate": False,
        "requote_hold_queue_shares": 500.0,
        "requote_hold_below_target": 0.08,
        "description": "Loose baseline: raw dynamic offset (0.5x range), 1c floor, 20c ceiling, no widen snap-back, 15c score window",
    },
}


def apply_tournament_preset(config: MakerConfig, preset_name: str) -> MakerConfig:
    """Return a new MakerConfig instance with the specified tournament preset applied."""
    if preset_name not in TOURNAMENT_PRESETS:
        raise ValueError(
            f"Unknown tournament preset {preset_name!r}. "
            f"Available presets: {list(TOURNAMENT_PRESETS.keys())}"
        )
    from dataclasses import replace
    preset = {k: v for k, v in TOURNAMENT_PRESETS[preset_name].items() if k != "description"}
    return replace(config, **preset)


def _env_get(canonical: str, *legacies: str) -> tuple[str | None, str | None]:
    """Return (value, var_name_used) checking canonical uppercase first, then legacy env vars."""
    val = os.environ.get(canonical)
    if val is not None and val.strip():
        return val, canonical
    for leg in legacies:
        val = os.environ.get(leg)
        if val is not None and val.strip():
            return val, leg
    return None, None


def _bounded_float(name: str, raw: str, lo: float, hi: float) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"{name}={raw!r} is not finite")
    if not lo <= value <= hi:
        raise ValueError(f"{name}={raw!r} is outside {lo:g}..{hi:g}")
    return value


def resolve_wide_book_trial(raw: str, *, for_display: bool = False, var_name: str = "HUNTER_WIDE_BOOK_TRIAL") -> float | None:
    if not raw.strip():
        return None
    if not rehearsal.is_rehearsal():
        if for_display:
            logging.getLogger(__name__).warning(
                f"{var_name} ignored for a display-only config load "
                "(this process is not a rehearsal); shipped max_book_spread stands.")
            return None
        raise ValueError(
            f"{var_name} widens max_book_spread, the gate that "
            "refuses a book where a fill may leave a leg that cannot be "
            "closed. It applies only in a process that has declared itself "
            "unable to place an order: core_brain.shadow_run, whose client "
            "cannot sign, or the ranker's --trial-spread, which only reads "
            "books and writes a market list.")
    return _bounded_float(var_name, raw, 0.0, 0.30)


def resolve_pair_cost_trial(raw: str, *, for_display: bool = False, var_name: str = "HUNTER_PAIR_COST_CAP") -> float:
    if not rehearsal.is_rehearsal():
        if for_display:
            logging.getLogger(__name__).warning(
                f"{var_name} ignored for a display-only config load "
                "(this process is not a rehearsal); shipped max_pair_cost stands.")
            return MakerConfig.max_pair_cost
        raise ValueError(
            f"{var_name} loosens max_pair_cost, the rule that stops a "
            "pair assembling above the $1.00 the instrument pays. It applies "
            "only in a process that has declared itself unable to place an "
            "order -- core_brain.shadow_run, whose client cannot sign.")
    return _bounded_float(var_name, raw, 0.0, 1.0)


def load(*, for_display: bool = False) -> MakerConfig:
    """Config, with the per-bot fields overridable from the environment."""
    return MakerConfig.from_env(for_display=for_display)


def couple_allocation_usd(cfg) -> float:
    """Dollars committed to one UP+DOWN couple, per the Owner's rule."""
    bankroll = getattr(cfg, "bot_quote_bank_baseline_usd", getattr(cfg, "bankroll_usd", 100.0))
    couple_pct = getattr(cfg, "bot_quote_alloc_couple_pct", getattr(cfg, "couple_risk_pct", 0.01))
    min_couple = getattr(cfg, "bot_quote_alloc_couple_min_usd", getattr(cfg, "min_couple_usd", 10.0))
    return max(bankroll * couple_pct, min_couple)


def leg_allocation_usd(cfg) -> float:
    """Dollars for one side of a couple. Half the couple, always."""
    return couple_allocation_usd(cfg) / 2.0


VENUE_MIN_NOTIONAL_USD: float = 1.0


def derive_dynamic_caps(cfg: MakerConfig, portfolio_usd: float | None = None) -> dict[str, float]:
    """Derive dynamic risk caps scaled to the account's total portfolio value.

    - max_naked_usd = 6% (naked_risk_pct) of portfolio value
    - max_order_usd = 25% (order_risk_pct) of portfolio value
    - max_total_usd = 90% (bankroll_ceiling_pct) of portfolio value
    """
    base_val = None
    if portfolio_usd is not None:
        try:
            val = float(portfolio_usd)
            if math.isfinite(val) and val > 0:
                base_val = val
        except (TypeError, ValueError):
            pass

    if base_val is None:
        try:
            val = float(getattr(cfg, "bot_quote_bank_baseline_usd", getattr(cfg, "bankroll_usd", 100.0)))
            base_val = val if (math.isfinite(val) and val > 0) else 100.0
        except (TypeError, ValueError):
            base_val = 100.0

    def _sanitize_pct(val, default_pct: float) -> float:
        try:
            fval = float(val)
            if math.isfinite(fval) and 0.0 < fval <= 1.0:
                return fval
        except (TypeError, ValueError):
            pass
        return default_pct

    naked_pct = _sanitize_pct(getattr(cfg, "bot_risk_exposure_max_naked_pct", getattr(cfg, "naked_risk_pct", 0.06)), 0.06)
    order_pct = _sanitize_pct(getattr(cfg, "bot_risk_exposure_max_order_pct", getattr(cfg, "order_risk_pct", 0.25)), 0.25)
    total_pct = _sanitize_pct(getattr(cfg, "bot_risk_exposure_bankroll_ceiling_pct", getattr(cfg, "bankroll_ceiling_pct", 0.90)), 0.90)

    raw_naked = base_val * naked_pct
    raw_order = base_val * order_pct
    raw_total = base_val * total_pct

    naked_val = round(raw_naked, 2) if raw_naked >= 0.005 else raw_naked
    order_val = round(raw_order, 2) if raw_order >= 0.005 else raw_order
    total_val = round(raw_total, 2) if raw_total >= 0.005 else raw_total

    floor = min(VENUE_MIN_NOTIONAL_USD, total_val)
    order_val = max(order_val, floor)

    return {
        "bankroll_usd": round(base_val, 2) if base_val >= 0.005 else base_val,
        "max_naked_usd": naked_val,
        "max_order_usd": order_val,
        "max_total_usd": total_val,
        "bot_quote_bank_baseline_usd": round(base_val, 2) if base_val >= 0.005 else base_val,
        "bot_risk_exposure_max_naked_usd": naked_val,
        "bot_risk_exposure_max_order_usd": order_val,
        "bot_risk_exposure_max_total_usd": total_val,
    }


MAX_ROTATION_INTERVAL_SEC = 1.0


def cap_rotation_interval(interval: float) -> float:
    """Clamp a requested rotation cadence to MAX_ROTATION_INTERVAL_SEC."""
    value = float(interval)
    if not math.isfinite(value) or value <= 0:
        logging.getLogger(__name__).warning(
            "invalid rotation interval %r (must be a finite positive "
            "number); using the %ss ceiling instead",
            interval, MAX_ROTATION_INTERVAL_SEC)
        return MAX_ROTATION_INTERVAL_SEC
    if value > MAX_ROTATION_INTERVAL_SEC:
        logging.getLogger(__name__).warning(
            "rotation interval %ss exceeds the %ss ceiling; clamped to the "
            "ceiling. Pass a SMALLER value to run faster -- a larger one is "
            "refused by design.", value, MAX_ROTATION_INTERVAL_SEC)
        return MAX_ROTATION_INTERVAL_SEC
    return value

"""Config for the spread hunter maker.

THE OBJECTIVE IS SPREAD CAPTURE: buy UP+DOWN below $1.00, merge the pair back to
collateral, keep the discount. Graduated spread markets carry $0.00 maker rewards;
income derives strictly from merge spread capture ($1.00 - pair cost). `bot_pos_objective` below names a
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
from dataclasses import dataclass
from pathlib import Path

from core_brain import rehearsal

from core_brain.single_leg_lifecycle import (
    DROP_CENTS_MAX,
    DROP_CENTS_MIN,
    STOP_LOSS_MODES,
)

ROOT = Path(__file__).resolve().parent.parent

#: Default floor between a hard stop and its own market being quotable again
#: (#796). `MakerConfig` reads it for the field default; the trader's
#: re-qualification gate and `release_hard_stop_blocks` read the field.
DEFAULT_HARD_STOP_LOCKOUT_SEC: float = 900.0

@dataclass(frozen=True)
class MakerConfig:
    # =========================================================================
    # 5 PROFILE FIELDS (declared directly on core so PICKER_PROFILE matches)
    # =========================================================================
    bot_quote_bank_baseline_usd: float = 100.0
    bot_risk_exposure_max_naked_usd: float = 6.0
    bot_quote_size_min_shares: int = 5
    # THE QUOTE WINDOW. `bot_quote_book_max_spread_from_mid` is the largest allowed distance
    # between the book midpoint and our resting quote. Prices are dollars on the
    # 0-1 scale; the env value is cents. The quoting engine refuses or skips a
    # quote outside the window and clamps the dynamic offset to it. It is NOT a
    # filter on which markets we quote -- `filter_common_spread_max_cents`
    # (2.05c) is the wide-book rejection bar (see CONSTRAINTS.md). The two
    # shipped defaults are deliberate: 0.15 for the engine, 0.045 for the
    # picker, whose copy the ranker does not gate on; both loaders read the
    # same variable the same way.
    bot_quote_book_max_spread_from_mid: float = 0.15
    bot_pos_price_max_pair_cost_usd: float = 0.99
    bot_quote_book_max_spread_cents: float = 0.06

    # =========================================================================
    # SCOPE 1: MARKET FILTERING (MARKET SCREENER)
    # =========================================================================
    # 1. Active / In-Play Markets (Live Sports & Esports)
    filter_live_vol_min_24h_usd: float = 35_000.0
    # THE MOVEMENT GATE (#74, #416). 24h volume says a market traded SOMETIME;
    # it says nothing about whether anything is happening now. A shadow run sat
    # 4.3 hours on a market with 1,777 shares ahead at 0.23 and zero traded --
    # inside every existing bar, tying up resting capital the whole time.
    # `filter_live_vel_lookback_sec` is the lookback, `filter_live_vel_min_usd`
    # the notional that must have changed hands inside it.
    #
    # ENFORCED 2026-09-08 (operator directive, unified-universe redesign).
    # Shipped 2026-08 at 0.0 -- record only -- so the bar could be chosen from
    # the recorded `movement_usd` column. The unified universe now pays one tape
    # read per volume-qualified market BEFORE its two book fetches, so a stale
    # market costs one request instead of three, and a recorded metric nobody
    # acted on became a real gate.
    #
    # WIDENED 2026-10-08 (#416). $500/30m refused ordinary live-sports point
    # swings: a 5c-8c tennis move on modest size prints roughly $100-300 of
    # notional per 30m, so an active match read as a stall. $200 keeps a wide
    # margin above a true stall ($0 printed for hours) while admitting a
    # normally-trading sports book, and stays far below the $125k/24h volume bar
    # -- whose markets average ~$2.6k per 30 minutes. The range gate below is
    # ANDed with this one, so a one-tick flicker still needs real notional.
    # Operator-tunable without code via FILTER_LIVE_VEL_MIN_USD and
    # FILTER_LIVE_VEL_LOOKBACK_SEC. Unmeasured tape stays fail-open:
    # `movement_reject` passes None, because a failed HTTP call must not empty
    # the universe on one bad minute.
    filter_live_vel_lookback_sec: float = 1800.0        # 30m lookback window
    filter_live_vel_min_usd: float = 200.0
    # Volatility / range gate (#416, #434). 2.0c minimum swing over the
    # volatility window refuses flat markets while active live sports/eSports
    # are exempted and prioritized. That window is distinct from the 30-minute
    # movement window above -- 2h by default. Operator-tunable via
    # FILTER_LIVE_MOVE_MIN_RANGE_CENTS and FILTER_LIVE_MOVE_LOOKBACK_SEC.
    filter_live_move_lookback_sec: float = 7200.0       # 2h lookback window
    filter_live_move_min_range_cents: float = 2.0
    filter_live_gate_velocity_on: bool = True
    # RELATIVE ACTIVITY RATIO GATE (loose baseline). Absolute 24h volume is
    # misleading: a flat $1M politics market and an active $10k live sports game
    # both clear a volume floor, but only one is actually trading. This gate
    # measures RECENT tape against total size: movement_usd (last movement
    # window) / volume_24h >= threshold. Loose initial threshold (0.0 = off by
    # default) so the baseline run observes a wide pool before we tighten it.
    # Operator-tunable via FILTER_LIVE_ACTIVITY_MIN_RATIO. 0.0 disables the gate.
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
    # THE MAKER-QUEUE BAR (#652 moved this from `scoring/config.py`; the picker
    # gated on it, the fleet did not). The three bars around it are FLOORS on
    # depth, and from the maker's side depth near mid IS the queue ahead of us --
    # the depth bars protect our ability to EXIT, which is real, and both point
    # the wrong way for queueing. 24h volume measures market-wide tape across
    # every price, so a market can clear six figures a day while trading nothing
    # at `mid - 2.5c`, the only level our order ever sits at. None of the three
    # measures the resting size at OUR price against the volume at OUR price.
    #
    # Session B of run-2809a7161de1: 344 orders, 10 markets, 30 minutes, zero
    # fills, and not one order's queue drained. Minutes of that market's own
    # observed volume needed to clear its median queue -- bonzi-halys 686,
    # bellucc-darderi 837, kouame-basavar 924, bolt-ruiz 12,332, rocha-mmoh
    # 50,397, and four markets with no observed trade at our price at all.
    #
    # 15 minutes is anchored to `bot_risk_pairs_exit_window_sec` = 900: a queue
    # that cannot clear inside the window where a naked leg is still called
    # fresh is a queue we should never join. 0 disables, as everywhere else.
    filter_std_queue_max_clear_min: float = 15.0
    # RECORD-ONLY FIRST. At 15 minutes the measured universe scores 686 at
    # best, so enforcing on day one refuses every market and takes the bot
    # silent. That is correct for the run we measured and still the wrong way
    # to ship it: record the number for a day, then decide with real data.
    filter_std_gate_queue_max_on: bool = False
    # Sister bar: the placement-time queue-clear gate
    # (`bot_order_queue_max_clear_min` = 60.0, `bot_order_gate_queue_clear_on`
    # = False) judges the same queue minutes at the bid actually posted. Same
    # arithmetic (`scoring/selector.py`), different moment -- see
    # `docs/issues/398-maker-queue-bars.md` for why the two stay separate.

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
    # Fill-quality entry gate (#761). Off by default: it cannot change live
    # behavior until the operator flips it AND blocks a bucket that cleared
    # the 8.9:1 break-even lift. Only the intent-visible edge dimension is
    # enforceable at plan time; the other calibration dimensions are
    # diagnostic until their features ride on the intent.
    bot_entry_fill_quality_gate_on: bool = False
    bot_entry_fill_quality_blocked_edge: tuple[str, ...] = ()
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
    bot_order_poll_interval_sec: float = 1.0
    bot_order_gate_queue_clear_on: bool = False
    bot_order_queue_max_clear_min: float = 60.0
    bot_order_queue_flow_window_sec: float = 1800.0

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
    # A hard stop shuts its market to FRESH entries for this long (#796). The
    # stop already leaves a mark, but the gate that clears marks is the
    # picker's publication mtime, and a 15-minute sports market can be
    # re-listed seconds after the exit -- run shadow-01 re-entered
    # `bun-stu-moe` four minutes after stopping out of it, and the replacement
    # was itself liquidated. This is the floor: no publication younger than
    # the window may clear a hard-stop mark, and when the window ends the
    # block releases on its own (`release_hard_stop_blocks`), so a market the
    # feed stopped listing is not locked out for the rest of the run. Anchored
    # to `bot_risk_pairs_exit_window_sec`: inside 15 minutes a naked leg still
    # counts as fresh. 0 disables the floor, leaving the publication gate
    # alone as before.
    bot_risk_hard_stop_entry_lockout_sec: float = DEFAULT_HARD_STOP_LOCKOUT_SEC
    bot_risk_gate_aged_out_rescue_on: bool = True
    bot_risk_aged_out_rescue_lead_sec: float = 900.0
    bot_risk_gate_orphan_profit_close_on: bool = False
    bot_risk_orphan_profit_min_age_sec: float = 3600.0
    # Minimum gross profit fraction over cost basis (0.05 = 5%).
    bot_risk_orphan_min_profit_pct: float = 0.05
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
    # DERIVED VALUE
    # =========================================================================
    @property
    def filter_common_spread_max_cents(self) -> float:
        """The picking bar: the live gate times the picking share (#572).

        Read-only, and a price FRACTION rather than whole cents -- the same
        convention `bot_quote_price_band_min_cents` (0.10) and
        `bot_quote_spread_static_offset_cents` (0.020) already use. One
        derivation, so the rehearsal trial pass-through and the six-decimal
        rounding stay in one place.
        """
        if self.bot_quote_wide_book_trial is not None:
            return self.bot_quote_wide_book_trial
        return round(self.bot_quote_book_max_spread_cents * self.filter_std_spread_share_pct, 6)

    @property
    def tau_cancel_sec(self) -> float:
        return (self.bot_latency_net_oneway_ms + self.bot_latency_cancel_venue_ack_ms) / 1000.0

    @property
    def tau_post_sec(self) -> float:
        return (self.bot_latency_net_oneway_ms + self.bot_latency_post_venue_accept_ms) / 1000.0

    def db_path(self) -> Path:
        return Path(os.environ.get("SPREAD_HUNTER_DB") or str(ROOT / "hunter.db"))

    @classmethod
    def from_env(cls, *, for_display: bool = False) -> MakerConfig:
        """Config loader. Canonical uppercase env vars only."""
        kw: dict = {}
        preset_name = resolve_tournament_preset_name()
        if preset_name:
            kw.update({k: v for k, v in TOURNAMENT_PRESETS[preset_name].items() if k != "description"})

        cid = _env("BOT_MARKET_PINNED_CONDITION_ID")
        if cid and cid.strip():
            kw["bot_market_pinned_condition_id"] = cid.strip()
            kw["bot_market_title"] = _env("BOT_MARKET_TITLE") or ""
            kw["bot_market_url"] = _env("BOT_MARKET_URL") or ""

            ms = _env("BOT_QUOTE_SIZE_MIN_SHARES")
            if ms:
                kw["bot_quote_size_min_shares"] = int(float(ms))
                kw["bot_quote_size_shares"] = max(int(float(ms)), 120)

            sp = _env("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID")
            if sp:
                # Pinned has always been strict here. #707 makes the rejection
                # cover inf/nan and anything outside 1..99 cents as well, and
                # names the variable the operator actually set.
                kw["bot_quote_book_max_spread_from_mid"] = _bounded_float(
                    "BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", sp.strip(), 1.0, 99.0) / 100.0

            tk = _env("BOT_QUOTE_GRID_TICK_SIZE_USD")
            if tk:
                kw["bot_quote_grid_tick_size_usd"] = float(tk)

        raw_sp_general = _env("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID")
        sp_general = raw_sp_general or ""
        if sp_general.strip():
            try:
                cents = float(sp_general)
            except ValueError:
                # A typo stays inactive on this path (#690): not enforced-at-zero,
                # so a config that loads cleanly today cannot start crashing on one.
                cents = None
            if cents is not None:
                # #707: inf, nan and anything outside 1..99 cents are rejections,
                # never a clamp and never a silent ignore -- a silently ignored
                # value reads as "enforced" while permitting anything.
                kw["bot_quote_book_max_spread_from_mid"] = _bounded_float(
                    "BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID",
                    sp_general.strip(), 1.0, 99.0) / 100.0

        awg = _env("BOT_QUOTE_GATE_WIDEN_ON")
        if awg:
            kw["bot_quote_gate_widen_on"] = awg.strip().lower() not in ("0", "false", "off", "no")

        wide_raw = _env("BOT_QUOTE_WIDE_BOOK_TRIAL")
        wide = resolve_wide_book_trial(wide_raw or "", for_display=for_display,
                                       var_name="BOT_QUOTE_WIDE_BOOK_TRIAL")
        if wide is not None and wide > 0:
            kw["bot_quote_wide_book_trial"] = wide
            kw["bot_quote_book_max_spread_cents"] = wide
            window = kw.get("bot_quote_book_max_spread_from_mid",
                            cls.bot_quote_book_max_spread_from_mid)
            kw["bot_quote_book_max_spread_from_mid"] = max(window, wide)

        # Trial knobs: the screener keeps the shipped default (None) and fails open
        # (scoring/config.py:197-249), while the engine raises and fails loud naming the variable.
        trial = _env("FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL")
        if trial:
            kw["filter_common_vol_min_top3_depth_usd_trial"] = _bounded_float(
                "FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL", trial.strip(), 0.0, float("inf")
            )

        vtri = _env("FILTER_STD_VOL_MIN_24H_USD_TRIAL")
        if vtri:
            kw["filter_std_vol_min_24h_usd_trial"] = _bounded_float(
                "FILTER_STD_VOL_MIN_24H_USD_TRIAL", vtri.strip(), 0.0, float("inf")
            )

        pr = _env("BOT_RISK_GATE_PAIRS_RULE_ON")
        if pr:
            kw["bot_risk_gate_pairs_rule_on"] = pr.strip().lower() not in ("0", "false", "off")

        pcap = _env("BOT_POS_PRICE_MAX_PAIR_COST_USD")
        if pcap:
            kw["bot_pos_price_max_pair_cost_usd"] = resolve_pair_cost_trial(
                pcap, for_display=for_display, var_name="BOT_POS_PRICE_MAX_PAIR_COST_USD")

        ccap = _env("BOT_POS_PRICE_MAX_COMPLETE_USD")
        if ccap:
            kw["bot_pos_price_max_complete_usd"] = _bounded_float(
                "BOT_POS_PRICE_MAX_COMPLETE_USD", ccap, 0.0, 1.0)

        rdb = _env("BOT_ORDER_REQUOTE_DEAD_BAND_CENTS")
        if rdb:
            kw["bot_order_requote_dead_band_cents"] = _bounded_float(
                "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS", rdb, 0.0, 1.0)

        qcg = _env("BOT_ORDER_GATE_QUEUE_CLEAR_ON")
        if qcg:
            kw["bot_order_gate_queue_clear_on"] = qcg.strip().lower() not in ("0", "false", "off")

        fqg = _env("BOT_ENTRY_FILL_QUALITY_GATE_ON")
        if fqg:
            kw["bot_entry_fill_quality_gate_on"] = fqg.strip().lower() not in ("0", "false", "off", "no")

        fbe = _env("BOT_ENTRY_FILL_QUALITY_BLOCKED_EDGE")
        if fbe:
            kw["bot_entry_fill_quality_blocked_edge"] = tuple(
                part.strip() for part in fbe.split(",") if part.strip()
            )

        qcm = _env("BOT_ORDER_QUEUE_MAX_CLEAR_MIN")
        if qcm:
            kw["bot_order_queue_max_clear_min"] = _bounded_float(
                "BOT_ORDER_QUEUE_MAX_CLEAR_MIN", qcm, 0.1, 1440.0)

        prw = _env("BOT_QUOTE_PRICE_RISK_WIDEN")
        if prw:
            kw["bot_quote_price_risk_widen"] = _bounded_float(
                "BOT_QUOTE_PRICE_RISK_WIDEN", prw, 0.0, 1.0)

        cno = _env("BOT_LATENCY_NET_ONEWAY_MS")
        if cno:
            kw["bot_latency_net_oneway_ms"] = float(cno)

        cva = _env("BOT_LATENCY_CANCEL_VENUE_ACK_MS")
        if cva:
            kw["bot_latency_cancel_venue_ack_ms"] = float(cva)

        pva = _env("BOT_LATENCY_POST_VENUE_ACCEPT_MS")
        if pva:
            kw["bot_latency_post_venue_accept_ms"] = float(pva)

        bk = _env("BOT_QUOTE_BANK_BASELINE_USD")
        if bk:
            val = float(bk)
            if not math.isfinite(val) or val <= 0:
                raise ValueError(f"Bankroll override must be finite and strictly positive, got: {val}")
            kw["bot_quote_bank_baseline_usd"] = val
            kw["bot_risk_exposure_max_committed_usd"] = val

        slm = _env("BOT_RISK_STOP_MODE")
        if slm:
            mode = slm.strip().upper()
            if mode not in STOP_LOSS_MODES:
                choices = ", ".join(sorted(STOP_LOSS_MODES))
                raise ValueError(
                    f"BOT_RISK_STOP_MODE must be one of {choices}, got: {slm}"
                )
            kw["bot_risk_stop_mode"] = mode

        dc = _env("BOT_RISK_STOP_ADVERSE_DROP_CENTS")
        if dc:
            kw["bot_risk_stop_adverse_drop_cents"] = _bounded_float(
                "BOT_RISK_STOP_ADVERSE_DROP_CENTS", dc, DROP_CENTS_MIN, DROP_CENTS_MAX)

        for _can_name, _field in (
            ("BOT_RISK_STOP_MAX_DRAWDOWN_PCT", "bot_risk_stop_max_drawdown_pct"),
            ("BOT_RISK_CIRCUIT_MAX_DRAWDOWN_PCT", "bot_risk_circuit_max_drawdown_pct"),
        ):
            _raw = _env(_can_name)
            if _raw:
                _val = _bounded_float(_can_name, _raw, 0.0, 100.0)
                if _val <= 0.0:
                    raise ValueError(
                        f"{_can_name}={_raw!r} must be greater than zero"
                    )
                kw[_field] = _val

        hsl = _env("BOT_RISK_HARD_STOP_ENTRY_LOCKOUT_SEC")
        if hsl is not None:
            # 0 is a real value here (it disables the floor), so the window is
            # 0..24h rather than "positive only".
            kw["bot_risk_hard_stop_entry_lockout_sec"] = _bounded_float(
                "BOT_RISK_HARD_STOP_ENTRY_LOCKOUT_SEC", hsl, 0.0, 86_400.0)

        aorl = _env("BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC")
        if aorl:
            val = float(aorl)
            if not math.isfinite(val) or val < 0:
                raise ValueError(
                    "BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC must be finite and "
                    f"non-negative, got: {val}")
            kw["bot_risk_aged_out_rescue_lead_sec"] = val

        orph_gate = _env("BOT_RISK_GATE_ORPHAN_PROFIT_CLOSE_ON")
        if orph_gate:
            kw["bot_risk_gate_orphan_profit_close_on"] = orph_gate.strip().lower() in ("1", "true", "yes", "on")

        orph_age = _env("BOT_RISK_ORPHAN_PROFIT_MIN_AGE_SEC")
        if orph_age:
            kw["bot_risk_orphan_profit_min_age_sec"] = _bounded_float(
                "BOT_RISK_ORPHAN_PROFIT_MIN_AGE_SEC", orph_age, 1.0, 86400.0)

        orph_pct = _env("BOT_RISK_ORPHAN_MIN_PROFIT_PCT")
        if orph_pct:
            kw["bot_risk_orphan_min_profit_pct"] = _bounded_float(
                "BOT_RISK_ORPHAN_MIN_PROFIT_PCT", orph_pct, 0.0, 1.0)

        mqs = _env("BOT_QUOTE_SIZE_MAX_SHARES")
        if mqs:
            val = float(mqs)
            if not math.isfinite(val) or val <= 0:
                raise ValueError(f"BOT_QUOTE_SIZE_MAX_SHARES must be strictly positive, got: {val}")
            kw["bot_quote_size_max_shares"] = val

        sgt = _env("BOT_EVAL_STAT_GATE_THRESHOLD_PCT")
        if sgt:
            kw["bot_eval_stat_gate_threshold_pct"] = _bounded_float(
                "BOT_EVAL_STAT_GATE_THRESHOLD_PCT", sgt, -100.0, 100.0)

        sgb = _env("BOT_EVAL_STAT_GATE_BANKROLL_PCT")
        if sgb:
            kw["bot_eval_stat_gate_bankroll_pct"] = _bounded_float(
                "BOT_EVAL_STAT_GATE_BANKROLL_PCT", sgb, 0.0, 1.0)

        stc = _env("BOT_EVAL_STAT_GATE_TARGET_CLOSES")
        if stc:
            try:
                val = int(stc)
                if val <= 0:
                    raise ValueError
            except ValueError as exc:
                raise ValueError(
                    f"BOT_EVAL_STAT_GATE_TARGET_CLOSES must be a positive integer, got: {stc!r}"
                ) from exc
            kw["bot_eval_stat_gate_target_closes"] = val

        dyn = _env("BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON")
        if dyn:
            kw["bot_quote_gate_dynamic_offset_on"] = dyn.strip().lower() in ("1", "true", "yes", "on")

        dyn_mult = _env("BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER")
        if dyn_mult:
            kw["bot_quote_spread_offset_multiplier"] = _bounded_float(
                "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER", dyn_mult, 0.0, 5.0)

        dyn_min = _env("BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS")
        if dyn_min:
            kw["bot_quote_spread_offset_min_cents"] = _whole_cents(
                "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS", dyn_min, 1, 10)

        dyn_max = _env("BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS")
        if dyn_max:
            kw["bot_quote_spread_offset_max_cents"] = _whole_cents(
                "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS", dyn_max, 1, 20)

        dyn_age = _env("BOT_QUOTE_SPREAD_OFFSET_MAX_AGE_SEC")
        if dyn_age:
            kw["bot_quote_spread_offset_max_age_sec"] = _bounded_float(
                "BOT_QUOTE_SPREAD_OFFSET_MAX_AGE_SEC", dyn_age, 0.0, 86400.0)

        return cls(**kw)


# THE PICKER PROFILE (#571). The five settings the ranker deliberately runs at
# a different value from the live fleet, in one table so both sides of every
# disagreement can be read in one place. `scoring.config.MakerConfig` reads
# these as its own defaults and declares no literal of its own; the live values
# on the class above are untouched by them.
PICKER_PROFILE: dict[str, float | int] = {
    "bot_quote_bank_baseline_usd": 1000.0,
    "bot_risk_exposure_max_naked_usd": 120.0,
    "bot_quote_size_min_shares": 50,
    "bot_quote_book_max_spread_from_mid": 0.045,
    "bot_pos_price_max_pair_cost_usd": 0.995,
}


TOURNAMENT_PRESETS: dict[str, dict] = {
    "conservative": {
        "bot_quote_gate_dynamic_offset_on": True,
        "bot_quote_spread_offset_multiplier": 0.75,
        "bot_quote_spread_offset_min_cents": 2,
        "bot_quote_spread_offset_max_cents": 5,
        "description": "Wider safety buffer in volatile swings (2¢–5¢)",
    },
    "balanced": {
        "bot_quote_gate_dynamic_offset_on": True,
        "bot_quote_spread_offset_multiplier": 0.50,
        "bot_quote_spread_offset_min_cents": 2,
        "bot_quote_spread_offset_max_cents": 4,
        "description": "Proportional volatility tracking (2¢–4¢)",
    },
    "aggressive": {
        "bot_quote_gate_dynamic_offset_on": True,
        "bot_quote_spread_offset_multiplier": 0.25,
        "bot_quote_spread_offset_min_cents": 2,
        "bot_quote_spread_offset_max_cents": 3,
        "description": "Tight offset tracking capturing fast fills (2¢–3¢)",
    },
    "prudent": {
        "bot_quote_gate_dynamic_offset_on": True,
        "bot_quote_spread_offset_multiplier": 0.60,
        "bot_quote_spread_offset_min_cents": 2,
        "bot_quote_spread_offset_max_cents": 4,
        "description": "Hybrid conservative-balanced tracking (2¢–4¢, 0.60x)",
    },
    "hybrid": {
        "bot_quote_gate_dynamic_offset_on": False,
        "bot_order_requote_dead_band_cents": 0.060,
        "description": "Empirical Winner Hybrid: control edge + Deadband-Wide (6c deadband)",
    },
    "baseline": {
        "bot_quote_gate_dynamic_offset_on": True,
        "bot_quote_spread_offset_multiplier": 0.50,
        "bot_quote_spread_offset_min_cents": 1,
        "bot_quote_spread_offset_max_cents": 20,
        "bot_quote_book_max_spread_from_mid": 0.15,
        "bot_quote_gate_widen_on": False,
        "description": "Loose baseline: raw dynamic offset (0.5x range), 1c floor, 20c ceiling, no widen snap-back, 15c quote window",
    },
}


def resolve_tournament_preset_name() -> str | None:
    """The shared tournament-preset selector for the engine and the ranker (#687).

    `BOT_QUOTE_TOURNAMENT_PRESET`, trimmed and lowercased; `None` when it is
    unset or blank. An unknown name raises the engine's existing `ValueError`
    text, so a typo fails the same way on both loaders instead of silently
    running the ranker on default bars.
    """
    preset_name = (_env("BOT_QUOTE_TOURNAMENT_PRESET") or "").strip().lower()
    if not preset_name:
        return None
    if preset_name not in TOURNAMENT_PRESETS:
        raise ValueError(
            f"BOT_QUOTE_TOURNAMENT_PRESET={preset_name!r} is unknown. "
            f"Valid presets: {list(TOURNAMENT_PRESETS.keys())}"
        )
    return preset_name


def describe_active_profile(cfg, preset_name: str | None) -> dict:
    """The badge name for the engine configuration in use (#856).

    Pure: reads only its inputs, changes nothing. The name is the selected
    preset, plus `queue-clear-<N>m` when the queue gate is on, joined with
    `+`; `default` when neither applies. Minutes print without trailing
    zeros (`5.0` -> `5m`, `2.5` -> `2.5m`). `None` means "no named preset
    selected" -- it is never rendered as `baseline`, which is a real preset.
    No preset is ever inferred from matching numbers.
    """
    queue_on = bool(getattr(cfg, "bot_order_gate_queue_clear_on", False))
    try:
        queue_min = float(getattr(cfg, "bot_order_queue_max_clear_min", 0.0) or 0.0)
    except (TypeError, ValueError):
        queue_min = 0.0
    parts: list[str] = []
    if preset_name:
        parts.append(str(preset_name))
    if queue_on:
        parts.append(f"queue-clear-{queue_min:g}m")
    return {
        "name": "+".join(parts) if parts else "default",
        "preset": preset_name,
        "queue_clear_on": queue_on,
        "queue_max_clear_min": queue_min,
    }


# The ranker's own preset bars (#687). Same six names as TOURNAMENT_PRESETS so
# one selector drives the engine and the ranker together; scoring-only field
# overrides, never engine quoting fields and never the five PICKER_PROFILE
# fields (in particular `bot_quote_book_max_spread_from_mid`: the engine `baseline` preset
# sets 0.15 there, a quoting-window value that must not clobber the picker's
# deliberate 0.045 window). Entries with no overrides keep the shipped screener
# bars, and today that is EVERY entry, `baseline` included: no run record
# documents preset-specific screener numbers, and inventing a
# conservative-to-aggressive scale would be guessing numbers the strategy has
# never measured (#687 N1). If such numbers are ever measured, they arrive as
# an explicit operator decision and an override on the entry that earned them.
SCREENER_PRESETS: dict[str, dict] = {
    "conservative": {
        "description": "Uses shipped screener bars",
    },
    "balanced": {
        "description": "Uses shipped screener bars",
    },
    "aggressive": {
        "description": "Uses shipped screener bars",
    },
    "prudent": {
        "description": "Uses shipped screener bars",
    },
    "hybrid": {
        "description": "Uses shipped screener bars",
    },
    "baseline": {
        "description": "Uses shipped screener bars (no preset has measured bars)",
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


def _env(name: str) -> str | None:
    """The environment value for `name`, or None when unset or blank."""
    val = os.environ.get(name)
    if val is None or not val.strip():
        return None
    return val


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


def _whole_cents(name: str, raw: str, lo: int, hi: int) -> int:
    """An integer count of cents inside `lo`..`hi`, or a ValueError naming the key.

    Whole cents only. `6` is six cents; `6.5` and `6.0` are refused rather than
    truncated, because `_bounded_float(...)` followed by `int()` silently ran
    6 for both of them until #872 and the operator had no way to see it. The
    message names the unit so a reader never has to guess the scale.
    """
    text = raw.strip()
    if not (text.isdigit() or (text[:1] in ("+", "-") and text[1:].isdigit())):
        raise ValueError(
            f"{name}={raw!r} must be whole cents, for example 6 = 6\u00a2"
        )
    value = int(text)
    if not lo <= value <= hi:
        raise ValueError(
            f"{name}={raw!r} is outside {lo}..{hi} whole cents"
        )
    return value


def resolve_wide_book_trial(raw: str, *, for_display: bool = False, var_name: str = "BOT_QUOTE_WIDE_BOOK_TRIAL") -> float | None:
    if not raw.strip():
        return None
    if not rehearsal.is_rehearsal():
        if for_display:
            logging.getLogger(__name__).warning(
                f"{var_name} ignored for a display-only config load "
                "(this process is not a rehearsal); shipped bot_quote_book_max_spread_cents stands.")
            return None
        raise ValueError(
            f"{var_name} widens bot_quote_book_max_spread_cents, the gate that "
            "refuses a book where a fill may leave a leg that cannot be "
            "closed. It applies only in a process that has declared itself "
            "unable to place an order: core_brain.shadow_run, whose client "
            "cannot sign, or the ranker's --trial-spread, which only reads "
            "books and writes a market list.")
    return _bounded_float(var_name, raw, 0.0, 0.30)


def resolve_pair_cost_trial(raw: str, *, for_display: bool = False, var_name: str = "BOT_POS_PRICE_MAX_PAIR_COST_USD") -> float:
    if not rehearsal.is_rehearsal():
        if for_display:
            logging.getLogger(__name__).warning(
                f"{var_name} ignored for a display-only config load "
                "(this process is not a rehearsal); shipped bot_pos_price_max_pair_cost_usd stands.")
            return MakerConfig.bot_pos_price_max_pair_cost_usd
        raise ValueError(
            f"{var_name} loosens bot_pos_price_max_pair_cost_usd, the rule that stops a "
            "pair assembling above the $1.00 the instrument pays. It applies "
            "only in a process that has declared itself unable to place an "
            "order -- core_brain.shadow_run, whose client cannot sign.")
    return _bounded_float(var_name, raw, 0.0, 1.0)


def load(*, for_display: bool = False) -> MakerConfig:
    """Config, with the per-bot fields overridable from the environment."""
    return MakerConfig.from_env(for_display=for_display)


def couple_allocation_usd(cfg) -> float:
    """Dollars committed to one UP+DOWN couple, per the Owner's rule."""
    bankroll = cfg.bot_quote_bank_baseline_usd
    couple_pct = cfg.bot_quote_alloc_couple_pct
    min_couple = cfg.bot_quote_alloc_couple_min_usd
    return max(bankroll * couple_pct, min_couple)


def leg_allocation_usd(cfg) -> float:
    """Dollars for one side of a couple. Half the couple, always."""
    return couple_allocation_usd(cfg) / 2.0


VENUE_MIN_NOTIONAL_USD: float = 1.0


def derive_dynamic_caps(cfg: MakerConfig, portfolio_usd: float | None = None) -> dict[str, float]:
    """Derive dynamic risk caps scaled to the account's total portfolio value.

    - bot_risk_exposure_max_naked_usd = 6% (bot_risk_exposure_max_naked_pct) of portfolio value
    - bot_risk_exposure_max_order_usd = 25% (bot_risk_exposure_max_order_pct) of portfolio value
    - bot_risk_exposure_max_total_usd = 90% (bot_risk_exposure_bankroll_ceiling_pct) of portfolio value
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
            val = float(cfg.bot_quote_bank_baseline_usd)
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

    naked_pct = _sanitize_pct(cfg.bot_risk_exposure_max_naked_pct, 0.06)
    order_pct = _sanitize_pct(cfg.bot_risk_exposure_max_order_pct, 0.25)
    total_pct = _sanitize_pct(cfg.bot_risk_exposure_bankroll_ceiling_pct, 0.90)

    raw_naked = base_val * naked_pct
    raw_order = base_val * order_pct
    raw_total = base_val * total_pct

    naked_val = round(raw_naked, 2) if raw_naked >= 0.005 else raw_naked
    order_val = round(raw_order, 2) if raw_order >= 0.005 else raw_order
    total_val = round(raw_total, 2) if raw_total >= 0.005 else raw_total

    floor = min(VENUE_MIN_NOTIONAL_USD, total_val)
    order_val = max(order_val, floor)

    return {
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

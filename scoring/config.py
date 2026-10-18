"""The ranker's view of the shared config. Numbers derived from powerwinner's
measured fills.

Source: 56,768 of his BTC/ETH 5-min fills over 2026-07-14..21 (2,970 markets).
See research/powerwinner_analysis.md.

SINGLE SOURCE (#571, #652). Every setting the picker shares with the live fleet
-- quoting, risk, pacing, markout, economics, and since #652 the eight market
filter bars -- is declared once, on `core_brain.config.MakerConfig`, and
inherited here. This class declares only the five values the picker deliberately
runs at a different level, which it reads from `core_brain.config.PICKER_PROFILE`,
plus its own `db_path()`.

Do not copy a shared field back into this class. A second declaration is a
second number, and `tests/test_config_divergence_guard.py` fails on the first
one that drifts from core's -- including a copy that happens to hold the same
value today, which is the drift a values-only comparison would wave through.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

from core_brain import rehearsal
from core_brain.config import PICKER_PROFILE, MakerConfig as CoreMakerConfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class MakerConfig(CoreMakerConfig):
    """Everything core declares, plus the bars only the ranker gates on."""

    # --- the five deliberate differences ----------------------------------
    # Read from `core_brain.config.PICKER_PROFILE` rather than written as
    # literals here: the numbers belong beside the live values they differ
    # from, so both sides of each disagreement are read in one place.
    #
    # `bankroll_usd` is the paper-run wallet the picker funds candidates
    # against. `max_naked_usd` is the exposure bar its depth gate is sized
    # against -- 4x, per the $500 depth note in core -- NOT the $6 the Trader
    # enforces at 6% of live account value. The two modules disagree on that
    # number on purpose.
    bankroll_usd: float = PICKER_PROFILE["bankroll_usd"]
    max_naked_usd: float = PICKER_PROFILE["max_naked_usd"]

    # Market screener depth qualification & spread velocity filters:
    #   min_quote_shares: 50 shares minimum depth floor to filter thin order books.
    #   max_spread_from_mid: 4.5c maximum offset from mid to guarantee competitive,
    #   tight trading touches.
    #   max_pair_cost: 0.995 candidate pair-cost ceiling for candidate selection.
    # Note: Bot Engine operates strictly on pair-assembly merge arbitrage ($1.00 - pair cost).
    # Maker rewards/rebates are $0.00 in graduated spread markets; accrual is disabled.
    min_quote_shares: int = PICKER_PROFILE["min_quote_shares"]
    max_spread_from_mid: float = PICKER_PROFILE["max_spread_from_mid"]
    max_pair_cost: float = PICKER_PROFILE["max_pair_cost"]

    # --- the eight market filter bars are core's (#652) -------------------
    # `filter_live_vel_lookback_sec`, `filter_live_vel_min_usd`,
    # `filter_live_gate_velocity_on`, `filter_live_activity_min_ratio`,
    # `filter_live_move_lookback_sec`, `filter_live_move_min_range_cents`,
    # `filter_std_queue_max_clear_min` and `filter_std_gate_queue_max_on` were
    # declared here until #652; they are inherited now and their rationale lives
    # beside them in `core_brain/config.py`. The names this file used for them
    # (`select_max_queue_minutes`, `enforce_max_queue_minutes`,
    # `select_movement_window_sec`, `select_min_movement_usd`,
    # `select_velocity_gate_enabled`, `select_min_activity_ratio`,
    # `select_min_range_cents`, `select_volatility_window_sec`) still resolve
    # through `_LEGACY_TO_CANONICAL`, with a DeprecationWarning: the dashboard's
    # parameter cards and `scripts/filter_markets.py` read them.

    def db_path(self) -> Path:
        # The duplicate this ticket keeps on purpose (CONSTRAINTS §4):
        # `scoring.ROOT` and `core_brain.ROOT` are the same folder, so this and
        # the inherited copy resolve identically --
        # `tests/test_config_single_source.py` pins both under three env states.
        return Path(os.environ.get("SPREAD_HUNTER_DB") or os.environ.get("HUNTER_DB", str(ROOT / "hunter.db")))


def _bounded_float(name: str, raw: str, lo: float, hi: float) -> float:
    """One env override, parsed and bounded, or a ValueError naming the variable.

    The twin of `core_brain.config._bounded_float`, copied for the same reason
    the refusal below is copied. `float()` accepts "nan" and "inf" without
    complaint, and NaN poisons every comparison it touches: a ceiling set to
    NaN reads as enforced while permitting anything. Raising beats clamping --
    a silently clamped value is a config the operator believes is in force and
    is not.
    """
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"{name}={raw!r} is not finite")
    if not lo <= value <= hi:
        raise ValueError(f"{name}={raw!r} is outside {lo:g}..{hi:g}")
    return value


def resolve_wide_book_trial(raw: str) -> float | None:
    """Parse `HUNTER_WIDE_BOOK_TRIAL`, refusing outside a declared rehearsal.

    The gate is `rehearsal.is_rehearsal()`, and the choice of question matters.
    The obvious alternative -- "is a signing key reachable?" -- is wrong twice
    over. Too weak, because `.env` is the documented home for the key and
    `venue.signed_client` copies it into `os.environ` only when it builds a
    signer, which on the live path can happen after `load()` has run. Too
    strong, because `core_brain.shadow_run` cannot sign whatever sits in
    `.env`: it builds a credential-free client behind a deny-by-default proxy,
    so a key on disk says nothing about what that process can do. Gating on
    the key would refuse the one place this trial is safe while passing on a
    live trader whose key had not been read yet.

    So the question is whether this process can place an order, which is a
    property of the entrypoint. Once that is answered no, the key is
    irrelevant and is not consulted -- a second check there would be dead code
    wearing the costume of a safety layer.

    Refusing beats ignoring and beats clamping. An operator who sets this
    outside a rehearsal has asked for a widened SAFETY gate on the money path;
    dropping the override silently would leave them believing a trial is
    running when it is not. Raising stops the process at config load, before a
    client is built.

    The bound is 0.30. A binary market whose two sides are thirty cents apart
    is not a wide book, it is an absent one, and a value past that is a typo.
    """
    if not raw.strip():
        return None
    if not rehearsal.is_rehearsal():
        raise ValueError(
            "HUNTER_WIDE_BOOK_TRIAL widens max_book_spread, the gate that "
            "refuses a book where a fill may leave a leg that cannot be "
            "closed. It applies only in a process that has declared itself "
            "unable to place an order: core_brain.shadow_run, whose client "
            "cannot sign, or the ranker's --trial-spread, which only reads "
            "books and writes a market list.")
    return _bounded_float("HUNTER_WIDE_BOOK_TRIAL", raw, 0.0, 0.30)


def load() -> MakerConfig:
    """Config, with the per-bot fields overridable from the environment.

    Four bots run the same code against different markets; each gets its own
    HUNTER_DB, port and HUNTER_MARKET. Nothing else differs, so a difference in
    results is a difference in the MARKET, not in the settings.
    """
    kw: dict = {}
    cid = os.environ.get("HUNTER_MARKET", "").strip()
    if cid:
        kw["pinned_condition_id"] = cid
        kw["market_title"] = os.environ.get("HUNTER_TITLE", "")
        kw["market_url"] = os.environ.get("HUNTER_URL", "")
        # Long-dated markets do not close in 15 seconds, and the min-size floor
        # is per-market (20, 50, 100, 200) -- quoting under it earns nothing.
        ms = os.environ.get("HUNTER_MIN_QUOTE_SHARES")
        if ms is None:
            ms = os.environ.get("HUNTER_MIN_SIZE")
            if ms is not None and ms.strip():
                logging.getLogger(__name__).warning(
                    "HUNTER_MIN_SIZE is deprecated (#632); set HUNTER_MIN_QUOTE_SHARES instead."
                )
        if ms and ms.strip():
            kw["min_quote_shares"] = int(float(ms))
            kw["quote_shares"] = max(int(float(ms)), 120)
        sp = os.environ.get("HUNTER_MAX_SPREAD_CENTS")
        if sp is None:
            sp = os.environ.get("HUNTER_MAX_SPREAD")   # in cents, from the venue
            if sp is not None and sp.strip():
                logging.getLogger(__name__).warning(
                    "HUNTER_MAX_SPREAD is deprecated (#632); set HUNTER_MAX_SPREAD_CENTS instead."
                )
        if sp and sp.strip():
            kw["max_spread_from_mid"] = float(sp) / 100.0
        tk = os.environ.get("HUNTER_PRICE_TICK")
        if tk is None:
            tk = os.environ.get("HUNTER_TICK")
            if tk is not None and tk.strip():
                logging.getLogger(__name__).warning(
                    "HUNTER_TICK is deprecated (#632); set HUNTER_PRICE_TICK instead."
                )
        if tk and tk.strip():
            kw["price_tick"] = float(tk)
    wide = resolve_wide_book_trial(os.environ.get("HUNTER_WIDE_BOOK_TRIAL") or "")
    if wide is not None and wide > 0:
        # The trial moves ONE ceiling; the picker bar follows through the
        # derived property (#572), and the reward window is widened only if
        # the trial is wider than it already is.
        kw["wide_book_trial"] = wide
        kw["max_book_spread"] = wide
        window = kw.get("max_spread_from_mid", MakerConfig.max_spread_from_mid)
        kw["max_spread_from_mid"] = max(window, wide)
    trial = os.environ.get("HUNTER_DEPTH_TRIAL_USD") or ""
    if trial.strip():
        kw["select_min_top3_depth_usd_trial"] = float(trial)
    mvmt = os.environ.get("HUNTER_MIN_MOVEMENT_USD") or ""
    if mvmt.strip():
        try:
            bar = float(mvmt)
        except ValueError:
            bar = None
        # inf/nan are not bars. `inf` would refuse every market on earth and
        # `nan` compares false against everything, silently disabling the gate.
        if bar is not None and math.isfinite(bar):
            kw["filter_live_vel_min_usd"] = max(0.0, bar)
    mwin = os.environ.get("HUNTER_MOVEMENT_WINDOW_SEC") or ""
    if mwin.strip():
        try:
            window = float(mwin)
        except ValueError:
            window = None
        if window is not None and math.isfinite(window) and window > 0:
            kw["filter_live_vel_lookback_sec"] = window
    vgate = os.environ.get("HUNTER_VELOCITY_GATE") or ""
    if vgate.strip():
        kw["filter_live_gate_velocity_on"] = vgate.strip().lower() not in ("0", "false", "off", "no")
    rcents = os.environ.get("HUNTER_MIN_RANGE_CENTS") or ""
    if rcents.strip():
        try:
            rc = float(rcents)
            if math.isfinite(rc) and rc >= 0:
                kw["filter_live_move_min_range_cents"] = rc
        except ValueError:
            pass
    vwin = os.environ.get("HUNTER_VOLATILITY_WINDOW_SEC") or ""
    if vwin.strip():
        try:
            vw = float(vwin)
            if math.isfinite(vw) and vw > 0:
                kw["filter_live_move_lookback_sec"] = vw
        except ValueError:
            pass
    vtri = os.environ.get("HUNTER_VOLUME_TRIAL_USD") or ""
    if vtri.strip():
        kw["select_min_volume_24h_usd_trial"] = float(vtri)
    # RELATIVE ACTIVITY RATIO GATE (loose baseline). movement_usd / volume_24h.
    # 0.0 (shipped default) disables it. Loose-baseline runs lower the absolute
    # volume floor AND turn this on, so relative recent activity -- not raw 24h
    # size -- is what admits a market.
    aratio = os.environ.get("HUNTER_MIN_ACTIVITY_RATIO") or ""
    if aratio.strip():
        try:
            ratio = float(aratio)
        except ValueError:
            ratio = 0.0
        if math.isfinite(ratio) and ratio >= 0:
            kw["filter_live_activity_min_ratio"] = ratio
    vol = os.environ.get("HUNTER_MIN_VOLUME_24H_USD") or ""
    if vol.strip():
        try:
            v = float(vol)
        except ValueError:
            v = 0.0
        if math.isfinite(v) and v >= 0:
            kw["select_min_volume_24h_usd"] = v
    pr = os.environ.get("HUNTER_PAIRS_RULE") or ""
    if pr.strip():
        kw["enable_pairs_rule"] = pr.strip().lower() not in ("0", "false", "off")
    cno = os.environ.get("HUNTER_NET_ONEWAY_MS") or os.environ.get("HUNTER_CANCEL_NET_ONEWAY_MS")
    if cno and cno.strip():
        kw["net_oneway_ms"] = float(cno)
    cva = os.environ.get("HUNTER_CANCEL_VENUE_ACK_MS")
    if cva and cva.strip():
        kw["cancel_venue_ack_ms"] = float(cva)
    pva = os.environ.get("HUNTER_POST_VENUE_ACCEPT_MS")
    if pva and pva.strip():
        kw["post_venue_accept_ms"] = float(pva)
    bk = os.environ.get("SPREAD_HUNTER_BANKROLL") or os.environ.get("HUNTER_BANKROLL") or ""
    if bk.strip():
        val = float(bk)
        if not math.isfinite(val) or val <= 0:
            raise ValueError(f"Bankroll override must be finite and strictly positive, got: {val}")
        kw["bankroll_usd"] = val
        kw["max_committed_usd"] = val
    return MakerConfig(**kw)

# hook probe
# hook probe

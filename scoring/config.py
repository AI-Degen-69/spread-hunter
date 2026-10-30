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

import math
import os
from dataclasses import dataclass

from core_brain import rehearsal
from core_brain.config import (
    PICKER_PROFILE,
    SCREENER_PRESETS,
    MakerConfig as CoreMakerConfig,
    _env,
    resolve_tournament_preset_name,
)
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
    # `bot_quote_bank_baseline_usd` is the paper-run wallet the picker funds
    # candidates against. `bot_risk_exposure_max_naked_usd` is the exposure bar
    # its depth gate is sized against -- 4x, per the $500 depth note in core --
    # NOT the $6 the Trader enforces at 6% of live account value. The two
    # modules disagree on that number on purpose.
    bot_quote_bank_baseline_usd: float = PICKER_PROFILE["bot_quote_bank_baseline_usd"]
    bot_risk_exposure_max_naked_usd: float = PICKER_PROFILE["bot_risk_exposure_max_naked_usd"]

    # Market screener depth qualification & spread velocity filters:
    #   bot_quote_size_min_shares: 50 shares minimum depth floor to filter thin
    #     order books.
    #   bot_pos_price_max_pair_cost_usd: 0.995 candidate pair-cost ceiling.
    # Note: Bot Engine operates strictly on pair-assembly merge arbitrage ($1.00 - pair cost).
    # Maker rewards/rebates are $0.00 in graduated spread markets; accrual is disabled.
    bot_quote_size_min_shares: int = PICKER_PROFILE["bot_quote_size_min_shares"]
    # THE QUOTE WINDOW. `bot_quote_book_max_spread_from_mid` is the largest allowed distance
    # between the book midpoint and our resting quote. Prices are dollars on the
    # 0-1 scale; the env value is cents. The quoting engine refuses or skips a
    # quote outside the window and clamps the dynamic offset to it. It is NOT a
    # filter on which markets we quote -- `filter_common_spread_max_usd`
    # (2.05c) is the wide-book rejection bar (see CONSTRAINTS.md). The two
    # shipped defaults are deliberate: 0.15 for the engine, 0.045 for the
    # picker, whose copy the ranker does not gate on; both loaders read the
    # same variable the same way.
    bot_quote_book_max_spread_from_mid: float = PICKER_PROFILE["bot_quote_book_max_spread_from_mid"]
    bot_pos_price_max_pair_cost_usd: float = PICKER_PROFILE["bot_pos_price_max_pair_cost_usd"]

    # --- the eight market filter bars are core's (#652) -------------------
    # `filter_live_vel_lookback_sec`, `filter_live_vel_min_usd`,
    # `filter_live_gate_velocity_on`, `filter_live_activity_min_ratio`,
    # `filter_live_move_lookback_sec`, `filter_live_move_min_range_cents`,
    # `filter_std_queue_max_clear_min` and `filter_std_gate_queue_max_on` were
    # declared here until #652; they are inherited now and their rationale lives
    # beside them in `core_brain/config.py`. The names this file used for them
    # are gone: the dashboard's parameter cards and `scripts/filter_markets.py`
    # read the canonical ones.

    def db_path(self) -> Path:
        # The duplicate this ticket keeps on purpose (CONSTRAINTS §4):
        # `scoring.ROOT` and `core_brain.ROOT` are the same folder, so this and
        # the inherited copy resolve identically --
        # `tests/test_config_single_source.py` pins both under three env states.
        return Path(os.environ.get("SPREAD_HUNTER_DB") or str(ROOT / "hunter.db"))


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


def resolve_wide_book_trial(raw: str, *, var_name: str = "BOT_QUOTE_WIDE_BOOK_TRIAL") -> float | None:
    """Parse `BOT_QUOTE_WIDE_BOOK_TRIAL`, refusing outside a declared rehearsal.

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
            f"{var_name} widens bot_quote_book_max_spread_usd, the gate that "
            "refuses a book where a fill may leave a leg that cannot be "
            "closed. It applies only in a process that has declared itself "
            "unable to place an order: core_brain.shadow_run, whose client "
            "cannot sign, or the ranker's --trial-spread, which only reads "
            "books and writes a market list.")
    return _bounded_float(var_name, raw, 0.0, 0.30)


def load() -> MakerConfig:
    """Config, with the per-bot fields overridable from the environment.

    Four bots run the same code against different markets; each gets its own
    SPREAD_HUNTER_DB, port and BOT_MARKET_PINNED_CONDITION_ID. Nothing else
    differs, so a difference in results is a difference in the MARKET, not in
    the settings.
    """
    kw: dict = {}
    # TOURNAMENT PRESET (#687). Defaults first, then the preset, then the
    # explicit environment reads below -- an explicit setting always wins over
    # the preset. The resolver raises on an unknown name, so a typo fails here
    # instead of silently scoring on default bars. Reads nothing from the five
    # PICKER_PROFILE fields, so the 0.045 quote window is never clobbered.
    preset_name = resolve_tournament_preset_name()
    if preset_name:
        kw.update({k: v for k, v in SCREENER_PRESETS[preset_name].items() if k != "description"})
    cid = _env("BOT_MARKET_PINNED_CONDITION_ID")
    cid = (cid or "").strip()
    if cid:
        kw["bot_market_pinned_condition_id"] = cid
        kw["bot_market_title"] = _env("BOT_MARKET_TITLE") or ""
        kw["bot_market_url"] = _env("BOT_MARKET_URL") or ""
        # The min-size floor is per-market (20, 50, 100, 200) -- quoting under
        # it earns nothing.
        ms = _env("BOT_QUOTE_SIZE_MIN_SHARES")
        if ms:
            kw["bot_quote_size_min_shares"] = int(float(ms))
            kw["bot_quote_size_shares"] = max(int(float(ms)), 120)
        tk = _env("BOT_QUOTE_GRID_TICK_SIZE_USD")
        if tk:
            kw["bot_quote_grid_tick_size_usd"] = float(tk)
    # THE SPREAD WINDOW IS READ ON EVERY RUN (#690, N2 of #674).
    #
    # `bot_quote_book_max_spread_from_mid` is how far from mid we allow our
    # resting quote to sit. The engine reads it on every run, through the
    # second tolerant read in `core_brain/config.py`; the ranker read it only
    # inside the pinned branch, so an unpinned run kept the 4.5c default while
    # the engine honoured the operator's setting. Same variable, two answers.
    #
    # The loader RULE is matched here, not the default: this class ships 4.5c
    # where core ships 15c, on purpose. Pinned stays strict -- `float()` raises,
    # the pinned contract since the beginning. Unpinned mirrors the engine:
    # malformed text is INACTIVE, not enforced-at-zero, so a typo neither crashes
    # a ranker that starts cleanly today nor narrows the window to something that
    # refuses every quote. Non-numbers are the only tolerated input: #707 makes
    # `inf`, `nan` and a number outside 1..99 cents a rejection on both paths, so
    # a value the operator set can never be silently dropped. That refusal is
    # loud on the money path and on this one; the dashboard's parameters endpoint
    # loads the engine config unguarded, so it fails instead of showing a made-up
    # number -- #709 owns turning that into a readable message.
    #
    # NOT the book gate. `filter_common_spread_max_usd` (2.05c) is the bid-ask
    # WIDTH wide books are refused on, derived from
    # `bot_quote_book_max_spread_usd * filter_std_spread_share_pct`. It must
    # never read this value: a distance-from-mid window and a bid-ask width are
    # different measurements.
    sp = _env("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID")
    if sp:
        if cid:
            kw["bot_quote_book_max_spread_from_mid"] = _bounded_float(
                "BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", sp.strip(), 1.0, 99.0) / 100.0
        else:
            try:
                cents = float(sp)
            except ValueError:
                cents = None
            if cents is not None:
                kw["bot_quote_book_max_spread_from_mid"] = _bounded_float(
                    "BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", sp.strip(), 1.0, 99.0) / 100.0
    wide_raw = _env("BOT_QUOTE_WIDE_BOOK_TRIAL")
    wide = resolve_wide_book_trial(wide_raw or "", var_name="BOT_QUOTE_WIDE_BOOK_TRIAL")
    if wide is not None and wide > 0:
        # The trial moves ONE ceiling; the picker bar follows through the
        # derived property (#572), and the quote window is widened only if
        # the trial is wider than it already is.
        kw["bot_quote_wide_book_trial"] = wide
        kw["bot_quote_book_max_spread_usd"] = wide
        window = kw.get("bot_quote_book_max_spread_from_mid",
                        MakerConfig.bot_quote_book_max_spread_from_mid)
        kw["bot_quote_book_max_spread_from_mid"] = max(window, wide)
    trial = _env("FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL")
    if trial:
        try:
            td = float(trial)
            if math.isfinite(td) and td >= 0:
                kw["filter_common_vol_min_top3_depth_usd_trial"] = td
        except ValueError:
            pass
    mvmt = _env("FILTER_LIVE_VEL_MIN_USD")
    if mvmt:
        try:
            bar = float(mvmt)
        except ValueError:
            bar = None
        # inf/nan are not bars. `inf` would refuse every market on earth and
        # `nan` compares false against everything, silently disabling the gate.
        if bar is not None and math.isfinite(bar):
            kw["filter_live_vel_min_usd"] = max(0.0, bar)
    mwin = _env("FILTER_LIVE_VEL_LOOKBACK_SEC")
    if mwin:
        try:
            window = float(mwin)
        except ValueError:
            window = None
        if window is not None and math.isfinite(window) and window > 0:
            kw["filter_live_vel_lookback_sec"] = window
    vgate = _env("FILTER_LIVE_GATE_VELOCITY_ON")
    if vgate:
        kw["filter_live_gate_velocity_on"] = vgate.strip().lower() not in ("0", "false", "off", "no")
    rcents = _env("FILTER_LIVE_MOVE_MIN_RANGE_CENTS")
    if rcents:
        try:
            rc = float(rcents)
            if math.isfinite(rc) and rc >= 0:
                kw["filter_live_move_min_range_cents"] = rc
        except ValueError:
            pass
    vwin = _env("FILTER_LIVE_MOVE_LOOKBACK_SEC")
    if vwin:
        try:
            vw = float(vwin)
        except ValueError:
            vw = None
        if vw is not None and math.isfinite(vw) and vw > 0:
            kw["filter_live_move_lookback_sec"] = vw
    vtri = _env("FILTER_STD_VOL_MIN_24H_USD_TRIAL")
    if vtri:
        try:
            vt = float(vtri)
            if math.isfinite(vt) and vt >= 0:
                kw["filter_std_vol_min_24h_usd_trial"] = vt
        except ValueError:
            pass
    # RELATIVE ACTIVITY RATIO GATE (loose baseline). movement_usd / volume_24h.
    # 0.0 (shipped default) disables it. Loose-baseline runs lower the absolute
    # volume floor AND turn this on, so relative recent activity -- not raw 24h
    # size -- is what admits a market.
    aratio = _env("FILTER_LIVE_ACTIVITY_MIN_RATIO")
    if aratio:
        try:
            ratio = float(aratio)
        except ValueError:
            ratio = 0.0
        if math.isfinite(ratio) and ratio >= 0:
            kw["filter_live_activity_min_ratio"] = ratio
    vol_live = _env("FILTER_LIVE_VOL_MIN_24H_USD")
    if vol_live:
        try:
            vl = float(vol_live)
            if math.isfinite(vl) and vl >= 0:
                kw["filter_live_vol_min_24h_usd"] = vl
        except ValueError:
            pass
    vol = _env("FILTER_STD_VOL_MIN_24H_USD")
    if vol:
        try:
            v = float(vol)
        except ValueError:
            v = 0.0
        if math.isfinite(v) and v >= 0:
            kw["filter_std_vol_min_24h_usd"] = v
    pr = _env("BOT_RISK_GATE_PAIRS_RULE_ON")
    if pr:
        kw["bot_risk_gate_pairs_rule_on"] = pr.strip().lower() not in ("0", "false", "off")
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
    return MakerConfig(**kw)

# hook probe
# hook probe

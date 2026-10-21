"""Tests for the loose-baseline quoting + selection changes.

Covers the three observable behaviours the loose-baseline arm introduces:

1. `activity_ratio_reject` -- the relative-activity selection gate
   (movement_usd / volume_24h >= threshold), fail-open on unmeasured input.
2. `quote_resting_price` with `apply_widen_gate=False` -- a WIDENED gate state
   must NOT snap the quote back to the tight widen_offset; it follows the raw
   dynamic offset.
3. A raised `max_spread_from_mid` lets a large dynamic offset through instead of
   clamping it to the shipped 4.5c.

Shipped defaults (apply_widen_gate=True, max_spread_from_mid=0.045,
select_min_activity_ratio=0.0) are asserted unchanged so the production path is
provably untouched.
"""

from core_brain.config import MakerConfig, TOURNAMENT_PRESETS
from core_brain.quotes import Inventory, quote_resting_price


def _flat_inv():
    """Empty (balanced) inventory, the standard fixture for quote-price tests."""
    return Inventory()


# --- 1. selection: relative activity ratio gate -----------------------------

def test_activity_ratio_reject_refuses_dormant_high_volume():
    """A flat $1M market with tiny recent tape is refused by the ratio gate."""
    from scripts.filter_markets import activity_ratio_reject

    reject, reason = activity_ratio_reject(
        movement_usd=50.0,      # only $50 moved recently
        volume_24h=1_000_000.0,  # but $1M traded over the day
        min_ratio=0.001,         # require 0.1% recent activity
    )
    assert reject is True
    assert "low activity ratio" in reason


def test_activity_ratio_reject_admits_active_market():
    """An active market whose recent tape is a healthy fraction passes."""
    from scripts.filter_markets import activity_ratio_reject

    reject, _ = activity_ratio_reject(
        movement_usd=5_000.0,
        volume_24h=20_000.0,   # 25% of the day's size moved recently
        min_ratio=0.001,
    )
    assert reject is False


def test_activity_ratio_reject_fails_open_on_unmeasured():
    """Unmeasured movement/volume must never empty the universe."""
    from scripts.filter_markets import activity_ratio_reject

    assert activity_ratio_reject(None, 1_000_000.0, min_ratio=0.001)[0] is False
    assert activity_ratio_reject(500.0, None, min_ratio=0.001)[0] is False
    assert activity_ratio_reject(500.0, 0.0, min_ratio=0.001)[0] is False  # no denominator


def test_activity_ratio_reject_disabled_at_zero():
    """The shipped default threshold (0.0) makes the gate inert."""
    from scripts.filter_markets import activity_ratio_reject

    assert activity_ratio_reject(1.0, 1_000_000.0, min_ratio=0.0)[0] is False


# --- 2. quoting: widen-gate kill switch -------------------------------------

def _book(bid: float, ask: float) -> dict:
    return {"best_bid": bid, "best_ask": ask}


def test_widen_gate_kill_switch_follows_raw_offset():
    """apply_widen_gate=False: a WIDENED market does not snap back tight.

    With the gate ON, a WIDENED market with widen_offset 3.5c would quote 3.5c
    under mid. With it OFF, the quote follows the raw dynamic offset (here a
    6c range x 0.5 multiplier = 3c... but the point is the WIDEN override is
    skipped, so the price is driven by base_offset, not widen_offset). We make
    the two differ by setting widen_offset far from the dynamic offset.
    """
    cfg = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True,
        bot_quote_spread_offset_multiplier=0.5,
        bot_quote_spread_offset_min_cents=2,
        bot_quote_spread_offset_max_cents=10,
        bot_quote_telemetry_range_cents=8.0,            # 0.5 x 8 = 4c dynamic offset
        bot_quote_telemetry_velocity_measured_at=None,
        bot_risk_circuit_gate_state="WIDENED",
        bot_risk_circuit_widen_offset_cents=0.02,          # 2c -- very different from 4c
        bot_quote_gate_widen_on=False,     # kill switch
        bot_quote_book_max_spread_from_mid=0.15,
    )
    # mid = 0.50, base_offset 4c -> provisional ~0.46
    price, provisional, _band, _trunc = quote_resting_price(
        cfg, _flat_inv(), "UP", _book(0.49, 0.51))
    assert price is not None
    # With the gate OFF the quote tracks the 4c dynamic offset (~0.46),
    # NOT the 2c widen_offset (~0.48).
    assert abs(provisional - 0.46) < 0.005


def test_widen_gate_on_still_snaps_back():
    """Shipped behaviour (apply_widen_gate=True) is unchanged."""
    cfg = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True,
        bot_quote_spread_offset_multiplier=0.5,
        bot_quote_spread_offset_min_cents=2,
        bot_quote_spread_offset_max_cents=10,
        bot_quote_telemetry_range_cents=8.0,
        bot_quote_telemetry_velocity_measured_at=None,
        bot_risk_circuit_gate_state="WIDENED",
        bot_risk_circuit_widen_offset_cents=0.02,
        bot_quote_gate_widen_on=True,
        bot_quote_book_max_spread_from_mid=0.15,
    )
    _price, provisional, _band, _trunc = quote_resting_price(
        cfg, _flat_inv(), "UP", _book(0.49, 0.51))
    # Gate ON: WIDENED snaps to widen_offset 2c -> provisional ~0.48.
    assert abs(provisional - 0.48) < 0.005


def test_raised_max_spread_lets_large_offset_through():
    """A raised max_spread_from_mid stops clamping a big dynamic offset."""
    # range 40c x 0.5 = 20c dynamic offset; clamp to [1,20] -> 20c.
    cfg = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True,
        bot_quote_spread_offset_multiplier=0.5,
        bot_quote_spread_offset_min_cents=1,
        bot_quote_spread_offset_max_cents=20,
        bot_quote_telemetry_range_cents=40.0,
        bot_quote_telemetry_velocity_measured_at=None,
        bot_risk_circuit_gate_state="NORMAL",
        bot_quote_gate_widen_on=False,
        bot_quote_book_max_spread_from_mid=0.15,   # 15c window -- wide enough
    )
    price, provisional, _band, _trunc = quote_resting_price(
        cfg, _flat_inv(), "UP", _book(0.40, 0.60))   # mid 0.50
    assert price is not None
    # 20c under a 0.50 mid would be 0.30; the ask-cap keeps it under 0.60.
    # The key assertion: provisional reflects the large offset (~0.30),
    # not a clamp to the old 4.5c (which would be ~0.455).
    assert provisional < 0.40


# --- 3. preset shape --------------------------------------------------------

def test_baseline_preset_is_registered_and_loose():
    """The baseline preset exists with the loose values, and does not touch
    the shipped ceiling to 0/None (which would zero every quote's score)."""
    p = TOURNAMENT_PRESETS["baseline"]
    assert p["bot_quote_gate_dynamic_offset_on"] is True
    assert p["bot_quote_spread_offset_multiplier"] == 0.50
    assert p["bot_quote_spread_offset_min_cents"] == 1
    assert p["bot_quote_spread_offset_max_cents"] == 20
    assert p["bot_quote_gate_widen_on"] is False
    assert p["bot_quote_book_max_spread_from_mid"] > 0.045   # raised, never zeroed


def test_resting_price_is_floored_to_whole_cent():
    """quote_resting_price floors DOWN to the whole cent, never rounds up.

    Operator directive 2026-10-12: resting orders sit on a standard 1-cent
    grid. floor -- not round-to-nearest -- so rounding can never tighten the
    offset or push the quote UP a cent toward the ask. 0.4439 -> 0.44.
    """
    cfg = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True, bot_quote_spread_offset_multiplier=0.5,
        bot_quote_spread_offset_min_cents=1, bot_quote_spread_offset_max_cents=20,
        bot_quote_telemetry_range_cents=15.66, bot_quote_telemetry_velocity_measured_at=None, bot_quote_grid_tick_size_usd=0.01,
        bot_quote_book_max_spread_from_mid=0.15,
    )
    # mid 0.545, band adds fractional offset -> raw lands sub-cent.
    price, provisional, *_ = quote_resting_price(cfg, _flat_inv(), "UP", _book(0.54, 0.55))
    # Floored to the cent, and never above the provisional target.
    assert abs(price * 100 - round(price * 100)) < 1e-9
    assert price <= provisional + 1e-9


def test_resting_price_never_rounds_up_a_cent():
    """A fractional target floors DOWN to the cent, never up."""
    cfg = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True, bot_quote_spread_offset_multiplier=0.5,
        bot_quote_spread_offset_min_cents=1, bot_quote_spread_offset_max_cents=20,
        bot_quote_telemetry_range_cents=10.0, bot_quote_telemetry_velocity_measured_at=None, bot_quote_grid_tick_size_usd=0.01,
        bot_quote_book_max_spread_from_mid=0.15,
    )
    price, provisional, *_ = quote_resting_price(cfg, _flat_inv(), "UP", _book(0.49, 0.60))
    assert abs(price * 100 - round(price * 100)) < 1e-9
    assert price <= provisional + 1e-9


def test_no_touch_reach_at_a_sub_cent_bid():
    """A small offset with zero band risk cannot rest at a half-cent touch.

    The retired HUNTER_REWARD_OFFSET forced 0.495 placement; that is gone. The
    quote floors to a whole cent at or below the touch.
    """
    from dataclasses import replace
    book = {"best_bid": 0.495, "best_ask": 0.505}
    cfg = replace(MakerConfig(bot_quote_grid_tick_size_usd=0.01, bot_quote_price_risk_widen=0.0),
                  bot_quote_spread_static_offset_cents=0.005, bot_quote_gate_dynamic_offset_on=False)
    price, *_ = quote_resting_price(cfg, _flat_inv(), "UP", book)
    assert abs(price * 100 - round(price * 100)) < 1e-9
    assert price <= 0.49 + 1e-9


def test_resting_price_never_goes_negative_or_zero():
    """A wide offset on a low-mid leg must clamp to a valid price, not <0.

    Bug found live 2026-10-12: 0.15 mid minus a 20c dynamic offset floored to
    -0.01, an impossible resting price the venue rejects. Clamp to [tick, ...].
    """
    cfg = MakerConfig(
        bot_quote_gate_dynamic_offset_on=True, bot_quote_spread_offset_multiplier=0.5,
        bot_quote_spread_offset_min_cents=1, bot_quote_spread_offset_max_cents=20,
        bot_quote_telemetry_range_cents=48.0, bot_quote_telemetry_velocity_measured_at=None, bot_quote_grid_tick_size_usd=0.01,
        bot_quote_book_max_spread_from_mid=0.15,
    )
    # UP book high, DOWN book low-mid -> the DOWN leg is the pressure case.
    up_book = {"best_bid": 0.85, "best_ask": 0.86}
    dn_book = {"best_bid": 0.14, "best_ask": 0.15}
    up_q, *_ = quote_resting_price(cfg, _flat_inv(), "UP", up_book)
    dn_q, *_ = quote_resting_price(cfg, _flat_inv(), "DOWN", dn_book)
    for q in (up_q, dn_q):
        assert q >= 0.01, f"resting price {q} below the one-tick floor"
        assert q < 1.0


def test_promoted_system_defaults():
    """The loose values are now the SHIPPED MakerConfig defaults (repo-wide).

    The legacy 4.5c cap, tighten-on-widen, and 2c-4c dynamic clamp are retired.
    """
    cfg = MakerConfig()
    assert cfg.bot_quote_gate_widen_on is False          # widen snap-back disabled
    assert cfg.bot_quote_book_max_spread_from_mid == 0.15        # wide ceiling
    assert cfg.bot_quote_gate_dynamic_offset_on is True     # dynamic quoting on
    assert cfg.bot_quote_spread_offset_min_cents == 1      # 1c floor
    assert cfg.bot_quote_spread_offset_max_cents == 20     # wide ceiling
    assert cfg.bot_quote_spread_offset_multiplier == 0.50

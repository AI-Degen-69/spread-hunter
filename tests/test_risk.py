"""Dollar-denominated risk primitives.

Every number in this file comes from the 2026-08-05 forensic reading, where
three share-denominated limits were armed and none of them bound: a 233.40
share position cost $190.26 against a nominal 360-share cap. These tests pin
the unit the limits are stated in, not the limits themselves.
"""
import dataclasses
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import risk                               # noqa: E402
from strategy.config import load as load_cfg            # noqa: E402
from strategy.quotes import Inventory                   # noqa: E402

BASE = load_cfg()


def _cfg(**kw):
    return dataclasses.replace(BASE, **kw)


def _book(bid, ask, depth=500.0):
    b = {"token_id": "TOK", "best_bid": bid, "best_ask": ask}
    if bid is not None:
        b["bids"] = {bid: depth}
    if ask is not None:
        b["asks"] = {ask: depth}
    return b


# --- naked_side / naked_usd -------------------------------------------------

def test_flat_inventory_has_no_naked_side():
    assert risk.naked_side(Inventory()) is None


def test_balanced_inventory_has_no_naked_side():
    inv = Inventory(up_shares=100.0, down_shares=100.0,
                    up_cost=52.0, down_cost=46.0)
    assert risk.naked_side(inv) is None


def test_naked_side_is_the_heavier_leg():
    inv = Inventory(up_shares=200.0, down_shares=100.0,
                    up_cost=120.0, down_cost=46.0)
    assert risk.naked_side(inv) == "UP"


def test_naked_usd_is_zero_for_a_balanced_book():
    inv = Inventory(up_shares=100.0, down_shares=100.0,
                    up_cost=52.0, down_cost=46.0)
    assert risk.naked_usd(inv, "UP") == 0.0
    assert risk.naked_usd(inv, "DOWN") == 0.0


def test_naked_usd_is_zero_on_the_light_side():
    inv = Inventory(up_shares=200.0, down_shares=100.0,
                    up_cost=120.0, down_cost=46.0)
    assert risk.naked_usd(inv, "DOWN") == 0.0


def test_naked_usd_reproduces_the_observed_loss():
    """lol-maz-mg1: 233.40 UP shares at an average of 0.8152 = $190.26.

    The share cap read 233 against a limit of 360 and stayed silent. The
    dollar reading is the one that describes what was at stake.
    """
    inv = Inventory(up_shares=233.40, down_shares=0.0,
                    up_cost=233.40 * 0.8152, down_cost=0.0)
    assert risk.naked_usd(inv, "UP") == pytest.approx(190.26, abs=0.01)


def test_naked_usd_values_only_the_excess_at_average_cost():
    """200 UP against 100 DOWN at a 0.60 UP average is $60 at risk, not $120.

    The hedged 100 shares pay exactly $1.00 whichever way the market resolves,
    so only the excess can go to zero.
    """
    inv = Inventory(up_shares=200.0, down_shares=100.0,
                    up_cost=120.0, down_cost=40.0)
    assert risk.naked_usd(inv, "UP") == pytest.approx(60.0)


def test_naked_usd_uses_average_cost_not_the_current_mark():
    """The amount at risk is what we paid. It must not shrink because the mid
    already moved against us -- that would loosen the cap exactly when the
    position is losing."""
    inv = Inventory(up_shares=100.0, down_shares=0.0,
                    up_cost=82.0, down_cost=0.0)
    assert risk.naked_usd(inv, "UP") == pytest.approx(82.0)


# --- risk_utilization -------------------------------------------------------

def test_utilization_is_zero_when_flat():
    assert risk.risk_utilization(_cfg(max_naked_usd=120.0), Inventory(), "UP") == 0.0


def test_utilization_is_the_dollar_fraction_of_the_budget():
    inv = Inventory(up_shares=100.0, down_shares=0.0, up_cost=60.0)
    got = risk.risk_utilization(_cfg(max_naked_usd=120.0), inv, "UP")
    assert got == pytest.approx(0.5)


def test_utilization_clamps_at_one_over_budget():
    inv = Inventory(up_shares=300.0, down_shares=0.0, up_cost=240.0)
    assert risk.risk_utilization(_cfg(max_naked_usd=120.0), inv, "UP") == 1.0


def test_a_zero_budget_disables_the_measure_rather_than_dividing_by_zero():
    """0 means unset, the same escape hatch every other cap in this config
    has. It must not read as 'infinite utilization'."""
    inv = Inventory(up_shares=300.0, down_shares=0.0, up_cost=240.0)
    assert risk.risk_utilization(_cfg(max_naked_usd=0.0), inv, "UP") == 0.0


# --- book_health ------------------------------------------------------------

def test_a_one_sided_book_is_unhealthy_and_says_so():
    res = risk.book_health(_book(0.52, None), _cfg())
    assert not res.ok
    assert "one-sided" in res.reason


def test_the_recorded_settled_book_is_refused():
    """wta-kalinsk-kessler ended quoting 0.999 bid against a 0.001 ask.

    There is no spread to capture on a market that has already decided, and
    the naked leg it would create is decided against us.
    """
    res = risk.book_health(_book(0.999, 0.001), _cfg())
    assert not res.ok
    assert "settled" in res.reason


def test_a_crossed_book_is_refused_outright():
    """A bid ABOVE the ask is not a wide book, it is an impossible one.

    Width could not catch it either way round: the raw `ba - bb` went
    NEGATIVE, and a negative number is never greater than the cap, while the
    absolute gap here is 5c -- comfortably inside the 6c bar. The recorded
    0.999/0.001 case was only ever stopped because the settled arm fires
    first; 0.60/0.55 sits nowhere near either end and reached the quoting
    path untouched.
    """
    res = risk.book_health(_book(0.60, 0.55, depth=5000.0), _cfg())
    assert not res.ok
    assert "crossed" in res.reason
    # The settled arm still owns the recorded case, which is crossed too.
    assert "settled" in risk.book_health(_book(0.999, 0.001), _cfg()).reason


def test_a_near_certain_one_way_book_is_refused_as_settled():
    res = risk.book_health(_book(0.997, 0.999), _cfg())
    assert not res.ok
    assert "settled" in res.reason


def test_a_wide_book_is_refused():
    """0.26/0.42 measured live: the whole 4.5c reward window lies inside the
    spread, so quoting in it means being the most exposed order in the book."""
    res = risk.book_health(_book(0.26, 0.42), _cfg())
    assert not res.ok
    assert "wide" in res.reason


def test_a_thin_book_is_refused():
    cfg = _cfg(min_book_depth_sh=200.0)
    res = risk.book_health(_book(0.52, 0.53, depth=50.0), cfg)
    assert not res.ok
    assert "thin" in res.reason


def test_a_healthy_book_is_accepted():
    res = risk.book_health(_book(0.52, 0.53, depth=500.0), _cfg())
    assert res.ok
    assert res.reason == ""


def test_missing_depth_is_reported_unevaluated_rather_than_failed():
    """Recorded history carries a mid but no depth (U7). A replay must be able
    to tell 'depth passed' apart from 'depth was never measured'."""
    book = {"token_id": "TOK", "best_bid": 0.52, "best_ask": 0.53}
    res = risk.book_health(book, _cfg())
    assert res.ok
    assert res.depth_evaluated is False


def test_present_depth_is_reported_evaluated():
    res = risk.book_health(_book(0.52, 0.53, depth=500.0), _cfg())
    assert res.depth_evaluated is True


# --- hard_block -------------------------------------------------------------
#
# One function, three arms, ordered so the reason names the cheapest certain
# rejection first. Every test below pins ONE arm and leaves the others passing,
# so a failure names the arm that broke.

HEALTHY = _book(0.52, 0.53)
HEDGE = _book(0.46, 0.47)


def _flat():
    return Inventory()


def test_a_healthy_pair_under_budget_is_not_blocked():
    assert risk.hard_block(_cfg(), _flat(), "UP", 0.50, HEALTHY, HEDGE) is None


def test_an_untradeable_hedge_token_blocks_the_side():
    """KTD2. A bid is safe only if the position it might create can be closed,
    and on a binary market it is closed by buying the OTHER token. A healthy
    own book says nothing about that."""
    why = risk.hard_block(_cfg(), _flat(), "UP", 0.50,
                          HEALTHY, _book(0.999, None))
    assert why is not None
    assert "hedge" in why and "DOWN" in why


def test_an_unquotable_own_book_blocks_the_side():
    why = risk.hard_block(_cfg(), _flat(), "UP", 0.50,
                          _book(0.26, 0.42), HEDGE)
    assert why is not None
    assert "own book" in why and "wide" in why


def test_the_hedge_arm_is_reported_ahead_of_the_own_arm():
    """Both books bad. The operator must read the hedge failure, because an
    unhedgeable market is the more certain rejection of the two."""
    why = risk.hard_block(_cfg(), _flat(), "UP", 0.50,
                          _book(0.26, 0.42), _book(0.999, None))
    assert "hedge" in why


def test_the_dollar_cap_blocks_at_the_budget_exactly():
    """>= not >. $120.00 of naked cost against a $120 budget is the cap
    reached, not the cap approached."""
    inv = Inventory(up_shares=200.0, down_shares=0.0, up_cost=120.0)
    why = risk.hard_block(_cfg(max_naked_usd=120.0), inv, "UP", 0.50,
                          HEALTHY, HEDGE)
    assert why is not None
    assert "120" in why


def test_one_cent_under_the_budget_is_not_blocked():
    """Isolates the cap: same shape, $119.99, and nothing else moved."""
    inv = Inventory(up_shares=200.0, down_shares=0.0, up_cost=119.99)
    assert risk.hard_block(_cfg(max_naked_usd=120.0), inv, "UP", 0.50,
                           HEALTHY, HEDGE) is None


def test_the_share_count_no_longer_decides_anything():
    """The unit is the whole point. 700 shares at 0.10 is $70 at risk and must
    pass a $120 budget; the share cap this replaces would have read 700."""
    inv = Inventory(up_shares=700.0, down_shares=0.0, up_cost=70.0)
    assert risk.hard_block(_cfg(max_naked_usd=120.0), inv, "UP", 0.50,
                           HEALTHY, HEDGE) is None


def test_a_zero_budget_disables_the_dollar_arm():
    """0 means unset, the same escape hatch every other cap in this config
    has -- not 'block everything'."""
    inv = Inventory(up_shares=300.0, down_shares=0.0, up_cost=240.0)
    assert risk.hard_block(_cfg(max_naked_usd=0.0), inv, "UP", 0.50,
                           HEALTHY, HEDGE) is None


def test_the_light_side_is_exempt_even_far_over_budget():
    """R4. The gates bound orders that ADD exposure. The light side is the only
    resting order that REDUCES it, so blocking it would freeze the position at
    maximum exposure with no route back down."""
    inv = Inventory(up_shares=300.0, down_shares=0.0, up_cost=240.0)
    assert risk.hard_block(_cfg(max_naked_usd=120.0), inv, "DOWN", 0.40,
                           HEDGE, HEALTHY) is None


def test_the_light_side_is_exempt_from_the_book_arms_too():
    """Same rule, stated on the arm that is easiest to get wrong: a market
    whose books have gone bad is exactly the market we most need to be able to
    hedge our way out of."""
    inv = Inventory(up_shares=300.0, down_shares=0.0, up_cost=240.0)
    assert risk.hard_block(_cfg(), inv, "DOWN", 0.40,
                           _book(0.26, 0.42), _book(0.999, None)) is None


def test_the_gate_is_switchable():
    """Same convention as enforce_price_band and enable_emergency_hedge: a new
    gate has to be measurable on its own, or a change in results cannot be
    attributed to it."""
    inv = Inventory(up_shares=300.0, down_shares=0.0, up_cost=240.0)
    assert risk.hard_block(_cfg(enable_hard_blocks=False), inv, "UP", 0.50,
                           _book(0.26, 0.42), _book(0.999, None)) is None


# --- hard_block: the price band (U4, R7) ------------------------------------
#
# `_in_band` has existed in `strategy/quotes.py` since the beginning and has
# never run on the objective the fleet actually quotes: it sits in the legacy
# branch of `decide_quotes`, BELOW the line where `_decide_quotes_rewards`
# returns. Telemetry reads exactly as an absent rule would -- fills averaged
# 0.8152 against a nominal 0.30-0.70 band.


def test_a_near_certain_price_is_refused_by_the_band():
    """0.935 is outside 0.30-0.70: the spread has collapsed toward one tick on
    an outcome the market already considers settled, so there is nothing to
    capture -- while a wrong resolution still costs the full $1.00."""
    why = risk.hard_block(_cfg(), _flat(), "UP", 0.935,
                          _book(0.95, 0.96), _book(0.03, 0.04))
    assert why is not None
    assert "band" in why


def test_turning_the_band_off_lets_the_same_price_through():
    """Isolates the band: nothing about the input moved except the switch."""
    assert risk.hard_block(_cfg(enforce_price_band=False), _flat(), "UP", 0.935,
                           _book(0.95, 0.96), _book(0.03, 0.04)) is None


def test_the_band_edges_are_inclusive():
    """Mirrors `_in_band`'s `low <= price <= high`. A rule that refused its own
    stated endpoints would quietly be a narrower rule than the config says."""
    cfg = _cfg(price_band_low=0.30, price_band_high=0.70)
    assert risk.hard_block(cfg, _flat(), "UP", 0.30, HEALTHY, HEDGE) is None
    assert risk.hard_block(cfg, _flat(), "UP", 0.70, HEALTHY, HEDGE) is None


def test_the_dollar_cap_is_reported_ahead_of_the_band():
    """Both bind. The operator must read the DOLLAR cap, because it describes
    what is already at stake rather than what a new fill would be worth."""
    inv = Inventory(up_shares=200.0, down_shares=0.0, up_cost=150.0)
    why = risk.hard_block(_cfg(max_naked_usd=120.0), inv, "UP", 0.935,
                          _book(0.95, 0.96), _book(0.03, 0.04))
    assert "naked" in why and "band" not in why


# --- hard_block: the pair-cost cap (U4, R7) ---------------------------------
#
# Same defect, same cause. `wta-kalinsk-kessler` bought 14 pairs at $1.0200 per
# pair against a $0.995 cap, on an instrument that pays exactly $1.00 -- a
# guaranteed 2c/share loss taken 14 times by a rule that was never reached.


def test_a_pair_over_the_cap_is_refused():
    """0.52 on UP against a 0.49 DOWN average is a $1.01 pair on a $1.00
    payout. Reproduces the $1.0200 pairs recorded on wta-kalinsk-kessler."""
    inv = Inventory(up_shares=100.0, down_shares=100.0,
                    up_cost=52.0, down_cost=49.0)
    why = risk.hard_block(_cfg(max_pair_cost=0.995), inv, "UP", 0.52,
                          HEALTHY, HEDGE)
    assert why is not None
    assert "pair" in why


def test_the_same_bid_against_a_cheaper_hedge_is_allowed():
    """Isolates the cap: the same 0.52 bid, the same shares, a 0.40 DOWN
    average -- a $0.92 pair, which is the trade this strategy exists to make."""
    inv = Inventory(up_shares=100.0, down_shares=100.0,
                    up_cost=52.0, down_cost=40.0)
    assert risk.hard_block(_cfg(max_pair_cost=0.995), inv, "UP", 0.52,
                           HEALTHY, HEDGE) is None


def test_the_pair_cap_binds_at_the_cap_exactly():
    """`>=` not `>`, matching the legacy comparison this restores and every
    other cap here: the cap is a ceiling reached, not approached."""
    inv = Inventory(up_shares=100.0, down_shares=100.0,
                    up_cost=50.0, down_cost=49.5)
    why = risk.hard_block(_cfg(max_pair_cost=0.995), inv, "UP", 0.50,
                          HEALTHY, HEDGE)
    assert why is not None and "pair" in why


def test_the_pair_cap_is_silent_with_no_hedge_leg_held():
    """`other_avg > 0` guards it, exactly as the legacy branch does. A zero
    average is 'we hold none of that token', not 'the hedge is free' -- without
    the guard every opening bid would look like a sub-$1.00 pair."""
    assert risk.hard_block(_cfg(max_pair_cost=0.995), _flat(), "UP", 0.50,
                           HEALTHY, HEDGE) is None


def test_the_light_side_keeps_its_exemption_over_both_new_arms():
    """R4 still rides above the gates. 300 UP against nothing is $240 at risk
    and the DOWN bid is the only resting order that reduces it, so neither the
    band nor the pair cap may refuse it -- refusing would freeze the position at
    maximum exposure with no route back down."""
    inv = Inventory(up_shares=300.0, down_shares=0.0, up_cost=240.0)
    assert risk.hard_block(_cfg(), inv, "DOWN", 0.95,
                           _book(0.96, 0.97), _book(0.03, 0.04)) is None


# --- band_risk_factor: variance and magnitude are two different risks --------
#
# Every gate above prices a position in dollars and none of them notices WHERE
# in the 0..1 range the fill lands. On a binary market two risks move in
# opposite directions across that range: per-share variance is p(1-p), which
# peaks at the coin flip, while the downside of one long share IS the price
# paid for it, which rises all the way to 1.00.


def test_the_size_cut_is_deepest_at_the_coin_flip():
    cfg = _cfg()
    assert risk.band_risk_factor(cfg, 0.50).size_mult == pytest.approx(
        1.0 - cfg.coinflip_size_cut)


def test_there_is_no_size_cut_a_halfwidth_away_from_the_coin_flip():
    """The taper reaches zero at `coinflip_halfwidth`, so the treatment is
    bounded rather than applying a little bit everywhere."""
    cfg = _cfg()
    for p in (0.5 + cfg.coinflip_halfwidth, 0.5 - cfg.coinflip_halfwidth, 0.95):
        assert risk.band_risk_factor(cfg, p).size_mult == pytest.approx(1.0)


def test_the_extra_offset_is_wider_at_0_68_than_at_0_32():
    """The two terms are told apart by their symmetry. 0.68 and 0.32 are the
    same distance from the coin flip, so the variance term is identical on
    both; the magnitude term is not, because $0.68 of downside per share is
    more than $0.32."""
    cfg = _cfg()
    assert (risk.band_risk_factor(cfg, 0.68).extra_offset
            > risk.band_risk_factor(cfg, 0.32).extra_offset)
    assert (risk.band_risk_factor(cfg, 0.68).size_mult
            == pytest.approx(risk.band_risk_factor(cfg, 0.32).size_mult))


def test_the_extra_offset_rises_with_price_outside_the_coinflip_zone():
    """Past the coin-flip taper only the magnitude term is left, and it must
    still respond: 0.90 buys more downside per share than 0.80."""
    cfg = _cfg()
    assert (risk.band_risk_factor(cfg, 0.90).extra_offset
            > risk.band_risk_factor(cfg, 0.80).extra_offset)


def test_a_zero_halfwidth_disables_the_variance_term_without_dividing_by_zero():
    """0 means unset, the same escape hatch every other tunable here has -- and
    it is the one value that would otherwise divide by zero at the coin flip."""
    cfg = _cfg(coinflip_halfwidth=0.0)
    assert risk.band_risk_factor(cfg, 0.50).size_mult == pytest.approx(1.0)


def test_a_zero_size_cut_leaves_the_multiplier_at_one_everywhere():
    cfg = _cfg(coinflip_size_cut=0.0)
    for p in (0.05, 0.32, 0.50, 0.68, 0.95):
        assert risk.band_risk_factor(cfg, p).size_mult == pytest.approx(1.0)


def test_the_multiplier_never_goes_negative():
    """A multiplier under zero would flip the sign of a size. Even a misconfigured
    cut past 1.0 must floor at 'quote nothing', never at 'quote backwards'."""
    cfg = _cfg(coinflip_size_cut=1.5)
    assert risk.band_risk_factor(cfg, 0.50).size_mult == 0.0


# --- size_for: the continuous ladder ----------------------------------------
#
# `hard_block` is a cliff by construction: $119.99 of naked cost quotes the
# full 120 shares and $120.00 quotes nothing. On lol-maz-mg1 that shape meant
# the last order placed before the cap was the largest one -- the position took
# its biggest single step toward the limit at the moment it was closest to it.
# The ladder replaces the step with base*(1-u)^2, so the order that arrives at
# 60% of the budget is 16% of full size rather than 100%.


def _long_up(usd: float, price: float = 0.60) -> Inventory:
    """UP-heavy inventory holding exactly `usd` of naked cost at `price`.

    Shares are derived from the dollars, not the other way round: the whole
    point of U3 is that the dollars are the input and the share count is a
    consequence of the price.
    """
    return Inventory(up_shares=(usd / price) if usd else 0.0, down_shares=0.0,
                     up_cost=usd, down_cost=0.0)


def test_size_is_full_when_nothing_is_at_risk():
    cfg = _cfg()
    full = max(cfg.quote_shares, cfg.min_quote_shares)
    assert risk.size_for(cfg, Inventory(), "UP", 0.50) == full


def test_size_is_zero_at_the_budget():
    """R5. The ladder reaches zero AT the budget, not one order past it."""
    cfg = _cfg(max_naked_usd=120.0)
    assert risk.size_for(cfg, _long_up(120.0), "UP", 0.60) == 0


def test_size_decays_monotonically_across_the_budget():
    """Steps of 0.2 utilization, nothing else moving.

    400 shares of base at a 0.20 average keeps the dollar-remainder cap well
    clear (96/0.20 = 480 shares of headroom at u=0.2), so this test reads the
    TAPER alone. The floor at min_quote_shares is what turns the last two rungs
    into zero: 400*(1-0.8)^2 = 16 shares would score nothing.
    """
    cfg = _cfg(quote_shares=400, min_quote_shares=50, max_naked_usd=120.0)
    sizes = [risk.size_for(cfg, _long_up(u * 120.0, 0.20), "UP", 0.20)
             for u in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
    assert sizes == [400, 256, 144, 64, 0, 0]
    assert sizes == sorted(sizes, reverse=True)


def test_the_decay_is_quadratic_not_linear():
    """Halfway to the budget must be BELOW half size.

    Linear would leave 200 shares resting with $60 of headroom left, i.e. an
    order that alone consumes two thirds of what remains. Quadratic leaves 100.
    """
    cfg = _cfg(quote_shares=400, min_quote_shares=50, max_naked_usd=120.0)
    half = risk.size_for(cfg, _long_up(60.0, 0.20), "UP", 0.20)
    assert half == 100
    assert half < 400 / 2


def test_a_size_under_the_venue_minimum_is_zero_rather_than_a_scoreless_order():
    """rewardsMinSize is 50: an order below it earns nothing and still buys
    inventory. 120*(1-0.5)^2 = 30 shares is exactly that order."""
    cfg = _cfg(max_naked_usd=120.0)          # base 120, min 50
    assert risk.size_for(cfg, _long_up(60.0, 0.50), "UP", 0.50) == 0


def test_just_above_the_floor_still_produces_an_order():
    """Isolates the floor: 120*(1-0.3)^2 = 58.8 shares clears 50, so the zero
    above is the floor talking and not the taper collapsing early."""
    cfg = _cfg(max_naked_usd=120.0)
    assert risk.size_for(cfg, _long_up(36.0, 0.50), "UP", 0.50) == 58


def test_the_dollar_remainder_caps_a_size_the_taper_would_still_allow():
    """One order must not be able to exceed the cap it is walking toward.

    $80 budget, $16 spent, so $64 of headroom -- and at 0.90 that is 71 shares.
    The taper alone would rest 76 (120*(1-0.2)^2 = 76.8), which would put $69
    of cost against $64 of room and land past the budget in a single fill.
    """
    cfg = _cfg(max_naked_usd=80.0)
    got = risk.size_for(cfg, _long_up(16.0, 0.90), "UP", 0.90)
    assert got == 71
    assert got < 76, "the taper alone would have allowed a larger order"
    assert got * 0.90 <= 80.0 - 16.0 + 1e-9


def test_the_light_side_never_tapers():
    """R4, in the sizing layer. The light side is the only resting order that
    FLATTENS the position, so tapering it would slow the exit exactly as the
    exposure peaked -- the failure the share cap already produced."""
    cfg = _cfg(max_naked_usd=120.0)
    full = max(cfg.quote_shares, cfg.min_quote_shares)
    heavy = Inventory(up_shares=400.0, down_shares=0.0, up_cost=240.0)
    assert risk.size_for(cfg, heavy, "DOWN", 0.40) == full


def test_a_zero_budget_leaves_the_size_full():
    """0 means unset, the same escape hatch every other cap here has. It must
    not read as 'fully utilized' and taper every order to nothing."""
    cfg = _cfg(max_naked_usd=0.0)
    full = max(cfg.quote_shares, cfg.min_quote_shares)
    assert risk.size_for(cfg, _long_up(500.0, 0.50), "UP", 0.50) == full


def test_an_unfunded_market_sizes_to_zero_rather_than_the_venue_minimum():
    """`quote_shares == 0` is the allocator saying 'do not quote this market'.
    `max(quote_shares, min_quote_shares)` used to promote it back to 50, which
    is how 17 defunded markets kept posting orders."""
    assert risk.size_for(_cfg(quote_shares=0), Inventory(), "UP", 0.50) == 0


def test_size_is_an_int():
    """QuoteIntent.size is an int and the venue takes whole shares."""
    got = risk.size_for(_cfg(max_naked_usd=120.0), _long_up(24.0, 0.60),
                        "UP", 0.60)
    assert isinstance(got, int)


def test_a_zero_price_does_not_divide_by_zero():
    """A degenerate book must produce no order, not a ZeroDivisionError: the
    remaining budget buys an unbounded number of shares at a price of zero,
    which is the one reading that must never reach the venue."""
    cfg = _cfg(max_naked_usd=120.0)
    assert risk.size_for(cfg, _long_up(24.0, 0.60), "UP", 0.0) == 0


# --- skew_offset: the spring, priced in dollars -----------------------------


def test_skew_is_zero_for_a_flat_inventory():
    assert risk.skew_offset(_cfg(), Inventory(), "UP") == 0.0
    assert risk.skew_offset(_cfg(), Inventory(), "DOWN") == 0.0


def test_skew_is_zero_when_exactly_balanced():
    """Balanced is not a small imbalance -- the hedged part pays exactly $1.00
    either way, so there is nothing to lean against."""
    inv = Inventory(up_shares=120.0, down_shares=120.0,
                    up_cost=62.0, down_cost=58.0)
    assert risk.skew_offset(_cfg(), inv, "UP") == 0.0
    assert risk.skew_offset(_cfg(), inv, "DOWN") == 0.0


def test_skew_pushes_the_heavy_side_out_and_pulls_the_light_side_in():
    cfg = _cfg(max_naked_usd=120.0)
    inv = _long_up(60.0, 0.60)                    # half the budget
    heavy = risk.skew_offset(cfg, inv, "UP")
    light = risk.skew_offset(cfg, inv, "DOWN")
    assert heavy > 0 > light
    assert heavy == pytest.approx(-light)
    assert heavy == pytest.approx(cfg.max_skew * 0.5)


def test_the_same_share_imbalance_skews_harder_at_a_higher_price():
    """THE UNIT, stated on the skew. 100 naked shares is $85 of downside at
    0.85 and $15 at 0.15, and only one of those is urgent. The share-denominated
    spring answered both identically, which is why it was still ramping on
    lol-maz-mg1 when the position was already fully built.
    """
    cfg = _cfg(max_naked_usd=120.0)
    dear = Inventory(up_shares=100.0, down_shares=0.0, up_cost=85.0)
    cheap = Inventory(up_shares=100.0, down_shares=0.0, up_cost=15.0)
    assert dear.up_shares == cheap.up_shares, "same SHARE imbalance by design"
    assert risk.skew_offset(cfg, dear, "UP") > risk.skew_offset(cfg, cheap, "UP")
    assert risk.skew_offset(cfg, dear, "UP") == pytest.approx(
        cfg.max_skew * 85.0 / 120.0)


def test_skew_is_capped_at_max_skew():
    """Utilization clamps at 1.0, so the spring bottoms out at the cap rather
    than walking a quote out of the 4.5c reward window."""
    cfg = _cfg(max_naked_usd=120.0)
    huge = Inventory(up_shares=100000.0, down_shares=0.0, up_cost=50000.0)
    assert risk.skew_offset(cfg, huge, "UP") == pytest.approx(cfg.max_skew)
    assert risk.skew_offset(cfg, huge, "DOWN") == pytest.approx(-cfg.max_skew)


def test_a_zero_budget_disables_the_skew_with_the_rest_of_the_dollar_rules():
    """0 unsets the dollar machinery wholesale, skew included. Anything else
    would leave a spring with no scale to measure itself against."""
    cfg = _cfg(max_naked_usd=0.0)
    assert risk.skew_offset(cfg, _long_up(500.0, 0.50), "UP") == 0.0

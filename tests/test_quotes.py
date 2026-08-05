"""Tests for the two powerwinner entry rules added to decide_quotes.

Both are switchable, and each is tested with the OTHER one off, so a passing
test can only be explained by the rule it names.
"""
import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import risk                             # noqa: E402
from strategy.config import load as load_cfg          # noqa: E402
from strategy.quotes import Inventory, decide_quotes  # noqa: E402

BASE = load_cfg()


def _cfg(**kw):
    """Config for the tests below, which all describe the "pair" objective.

    The band, the quoting window and the pair-cost cap are rules of that
    objective only. The default objective is now "rewards", which bypasses all
    three deliberately -- it is paid on resting size rather than on fill
    quality, so sitting out to protect a fill is what it must NOT do. Pinning
    the objective here keeps these tests honest about which rule set they
    cover; pass objective="rewards" explicitly to test the other one.
    """
    kw.setdefault("objective", "pair")
    return dataclasses.replace(BASE, **kw)


def _book(token, bid, ask):
    return {"token_id": token, "best_bid": bid, "best_ask": ask,
            "bids": {bid: 500.0}, "asks": {ask: 500.0}}


def _quote(cfg, up=(0.52, 0.53), dn=(0.46, 0.47), t_rem=200.0, frac=None,
           inv=None):
    return decide_quotes(cfg, _book("UPTOK", *up), _book("DNTOK", *dn),
                         inv or Inventory(), t_rem, frac)


# --- price band -------------------------------------------------------------

def test_mid_priced_market_is_quoted_when_only_the_band_is_on():
    intents, why = _quote(_cfg(enforce_quote_window=False))
    assert {q.side for q in intents} == {"UP", "DOWN"}, why


def test_near_certain_market_is_refused_by_the_band():
    """0.95/0.05 is outside 0.30-0.70: no spread to capture, full downside."""
    cfg = _cfg(enforce_quote_window=False)
    intents, why = _quote(cfg, up=(0.95, 0.96), dn=(0.03, 0.04))
    assert intents == []
    assert "outside band" in why


def test_turning_the_band_off_lets_the_same_market_through():
    """Isolates the band as the cause -- nothing else about the input moved."""
    cfg = _cfg(enforce_quote_window=False, enforce_price_band=False)
    intents, _ = _quote(cfg, up=(0.95, 0.96), dn=(0.03, 0.04))
    assert intents != []


# --- quoting window ---------------------------------------------------------

def test_quotes_early_in_the_window():
    intents, why = _quote(_cfg(enforce_price_band=False), frac=0.10)
    assert intents != [], why


def test_refuses_to_open_late_in_the_window():
    cfg = _cfg(enforce_price_band=False)
    intents, why = _quote(cfg, frac=0.80)
    assert intents == []
    assert "window" in why


def test_missing_window_clock_does_not_gate_quoting():
    """frac=None means 'unknown', and an unknown clock must not block every
    quote -- that would silently stop the bot rather than fail loudly."""
    intents, why = _quote(_cfg(enforce_price_band=False), frac=None)
    assert intents != [], why


def test_window_rule_off_allows_late_quotes():
    cfg = _cfg(enforce_price_band=False, enforce_quote_window=False)
    intents, _ = _quote(cfg, frac=0.80)
    assert intents != []


# --- the rules do not disturb the existing pair-cost guard ------------------

def test_pair_over_one_dollar_still_refused_inside_the_band():
    """Both legs in-band but the pair costs >= $1.00 on a $1.00 payout."""
    cfg = _cfg(enforce_quote_window=False)
    intents, why = _quote(cfg, up=(0.55, 0.56), dn=(0.45, 0.46))
    assert intents == []
    assert "sub-$1.00" in why


# --- rewards objective ------------------------------------------------------
#
# Measured on 2216 recorded book snapshots: the "pair" objective rested in the
# book on 31% of cycles and never once assembled a sub-$1.00 pair (median pair
# 1.010), because resting under the ASK puts each quote half a spread ABOVE
# mid. Quoting off MID instead reaches 86% in-book with a 0.960 median pair.
# These tests pin that behaviour.

def _rcfg(**kw):
    kw.setdefault("objective", "rewards")
    return dataclasses.replace(BASE, **kw)


def test_rewards_quotes_both_sides_under_mid():
    intents, why = _quote(_rcfg(reward_offset=0.02))
    assert {q.side for q in intents} == {"UP", "DOWN"}, why
    for q in intents:
        assert q.price < q.mid, "a reward quote must rest UNDER mid, never above"
        assert abs((q.mid - q.price) - 0.02) < 1e-6


def test_rewards_pair_is_under_one_dollar_by_construction():
    """The property the pair objective spent 60 markets failing to reach.

    mid_up + mid_down ~ 1.00, so bidding `offset` under mid on both sides costs
    ~1.00 - 2*offset. Nothing has to line up for this; it is arithmetic.
    """
    intents, _ = _quote(_rcfg(reward_offset=0.02))
    pair = sum(q.price for q in intents)
    mids = sum(q.mid for q in intents)
    assert pair < 1.0
    # Exactly 2*offset under the sum of the mids, whatever that sum happens to
    # be. On a real book the two mids sum to ~1.00 so the pair lands near 0.96;
    # asserting against `mids` states the mechanism rather than the fixture.
    assert abs(pair - (mids - 2 * 0.02)) < 1e-6


def test_rewards_does_not_sit_out_a_wide_or_late_market():
    """The gates that cost 69% of cycles must not apply to this objective."""
    # Pair over $1.00 at the touch, and 90% into the window: the pair objective
    # refuses both. Rewards are paid on resting size, so refusing earns zero.
    intents, why = _quote(_rcfg(), up=(0.55, 0.56), dn=(0.45, 0.46), frac=0.9)
    assert {q.side for q in intents} == {"UP", "DOWN"}, why


def test_rewards_never_crosses_the_spread():
    """A bid at/above the ask is a taker order: fee 0.07*p*(1-p) dwarfs the edge."""
    intents, _ = _quote(_rcfg(reward_offset=-0.05), up=(0.52, 0.53), dn=(0.46, 0.47))
    for q in intents:
        ask = 0.53 if q.side == "UP" else 0.47
        assert q.price < ask


def test_reward_score_is_quadratic_and_zero_outside_the_window():
    from strategy.quotes import reward_score
    cfg = _rcfg()
    v = cfg.max_spread_from_mid
    assert reward_score(cfg, 0.0, 120) == 120                  # at mid, full
    assert reward_score(cfg, v, 120) == 0                      # at the edge
    assert reward_score(cfg, v + 0.001, 120) == 0              # outside
    assert reward_score(cfg, 0.02, 10) == 0                    # under min size
    # Quadratic: half the max spread keeps a quarter of the score.
    assert abs(reward_score(cfg, v / 2, 120) - 0.25 * 120) < 1e-9


def test_rewards_skews_to_flatten_instead_of_dropping_a_side():
    """Long UP -> UP moves away from mid, DOWN moves toward it, both stay up.

    Replaces an earlier rule that stopped quoting the heavy side outright.
    That forfeited two thirds of the score (one-sided books score at 1/c,
    c=3.0) and still left the position lopsided. Skew keeps both sides
    resting, so the score is preserved AND the light side fills first.
    """
    # 60 UP at a 0.52 average is $31.20 of naked cost, 26% of the $120 budget:
    # this test is about the skew, so neither the dollar cap NOR the U3 size
    # taper (which floors a side to zero above ~35% utilization, at a 120-share
    # base) may be what decides the outcome. Both sides must still rest.
    inv = Inventory(up_shares=60.0, down_shares=0.0, up_cost=31.20, down_cost=0.0)
    intents, why = _quote(_rcfg(), inv=inv)
    assert {q.side for q in intents} == {"UP", "DOWN"}, why
    off = {q.side: q.mid - q.price for q in intents}
    assert off["UP"] > off["DOWN"], "heavy side must sit FURTHER from mid"
    # The light side is pulled toward mid, so it is the one that fills next.
    assert off["DOWN"] < 0.02


def test_skew_is_symmetric_and_flat_when_balanced():
    inv = Inventory(up_shares=120.0, down_shares=120.0, up_cost=60.0, down_cost=60.0)
    intents, _ = _quote(_rcfg(), inv=inv)
    off = {q.side: round(q.mid - q.price, 4) for q in intents}
    assert off["UP"] == off["DOWN"], "a flat book must not be skewed"


def test_never_outbids_the_book_by_more_than_a_tick():
    """On a wide book, mid-minus-offset lands far above the best bid.

    Measured live: a market quoting 0.26/0.42 has its whole 3.5c reward window
    inside the spread, so an uncapped quote bids 0.32 against a 0.26 best bid.
    That is the most exposed order in the book by six cents -- which is exactly
    why the window was empty. Rewards do not compensate for being picked off
    six cents wide.
    """
    cfg = _rcfg(price_tick=0.01)
    intents, _ = _quote(cfg, up=(0.26, 0.42), dn=(0.58, 0.74))
    for q in intents:
        best_bid = 0.26 if q.side == "UP" else 0.58
        assert q.price <= round(best_bid + cfg.price_tick, 4) + 1e-9, (
            f"{q.side} bid {q.price} outbids the book's {best_bid} by over a tick")


def test_tight_book_still_quotes_at_the_intended_offset():
    """The cap must not bite on a normal, tight book."""
    cfg = _rcfg()
    intents, _ = _quote(cfg, up=(0.52, 0.53), dn=(0.46, 0.47))
    for q in intents:
        assert abs((q.mid - q.price) - cfg.reward_offset) < 1e-6


# --- U2: the hard cap, in dollars -------------------------------------------
#
# The cap these cover used to be stated in SHARES, and the unit was the defect.
# On a binary market the downside of one long share is the price paid for it,
# so 360 shares permitted $72 of risk at 0.20 and $293 at 0.8152 -- loosest
# exactly where a wrong resolution costs most. Measured 2026-08-05 on
# lol-maz-mg1: 233.40 UP shares, $190.26 at risk, and the share cap read 233
# against 360 and stayed silent while 85% of the fleet's -$223.32 unhedged
# float sat in that one market.

def test_the_dollar_cap_stops_the_heavy_side_where_the_share_cap_did_not():
    """150 UP at a 0.82 average is $123 of naked cost against a $120 budget.

    The share cap this replaces would have read 150 against 360 and permitted
    the add -- the same blindness that let lol-maz-mg1 build to $190.26 with
    three limits armed and none binding.
    """
    inv = Inventory(up_shares=150.0, down_shares=0.0,
                    up_cost=123.0, down_cost=0.0)
    intents, why = _quote(_rcfg(max_naked_usd=120.0),
                          up=(0.82, 0.83), dn=(0.17, 0.18), inv=inv)
    sides = {q.side for q in intents}
    assert "UP" not in sides, f"heavy side must stop adding exposure: {why}"
    # The light side must keep quoting -- it is the only thing that flattens.
    assert "DOWN" in sides, f"light side must keep flattening: {why}"
    # And at FULL size: the side that reduces exposure must not be tapered.
    assert [q.size for q in intents if q.side == "DOWN"] == [120]


def test_one_dollar_under_the_budget_it_is_the_ladder_that_stops_the_add():
    """$119 against a $120 budget, same book and same share count as above.

    Before U3 this rested a full 120 shares -- which is the cliff R5 removes:
    the largest order of the market's life arriving with $1 of headroom left.
    The heavy side is still refused, and the two dollar rules are told apart
    directly rather than through the log, because `_decide_quotes_rewards`
    returns an empty reason whenever ANY side quotes and DOWN quotes here.
    """
    cfg = _rcfg(max_naked_usd=120.0)
    inv = Inventory(up_shares=150.0, down_shares=0.0,
                    up_cost=119.0, down_cost=0.0)
    intents, why = _quote(cfg, up=(0.82, 0.83), dn=(0.17, 0.18), inv=inv)
    assert "UP" not in {q.side for q in intents}, why
    assert "DOWN" in {q.side for q in intents}, why

    up_book = {"token_id": "UPTOK", "best_bid": 0.82, "best_ask": 0.83,
               "bids": {0.82: 500.0}, "asks": {0.83: 500.0}}
    dn_book = {"token_id": "DNTOK", "best_bid": 0.17, "best_ask": 0.18,
               "bids": {0.17: 500.0}, "asks": {0.18: 500.0}}
    # The BLOCK passes at $119 -- it is a `>=` against the budget and this is a
    # dollar under it. The LADDER is what refuses: 120*(1/120)^2 = 0.008
    # shares, floored to zero because anything under 50 scores nothing.
    assert risk.hard_block(cfg, inv, "UP", 0.795, up_book, dn_book) is None
    assert risk.size_for(cfg, inv, "UP", 0.795) == 0


def test_the_light_side_is_never_blocked_by_the_dollar_cap():
    """$180 of naked UP is 150% of the budget, and the DOWN bid is still the
    only resting order that brings it back down. R4: the gates bound orders
    that ADD exposure, never one that reduces it."""
    inv = Inventory(up_shares=300.0, down_shares=0.0,
                    up_cost=180.0, down_cost=0.0)
    intents, why = _quote(_rcfg(max_naked_usd=120.0),
                          up=(0.60, 0.61), dn=(0.39, 0.40), inv=inv)
    assert [q.side for q in intents] == ["DOWN"], why


def test_an_untradeable_hedge_token_blocks_the_healthy_side_too():
    """KTD2. The UP book here is a clean 0.52/0.53 with 500sh of depth, and UP
    must still not be quoted: DOWN is a 0.999 bid against no ask, the exact
    shape recorded on wta-kalinsk-kessler. A UP fill would create a naked leg
    that nothing on the venue could close, because on a binary market the only
    instrument that hedges UP is DOWN.
    """
    dn = {"token_id": "DNTOK", "best_bid": 0.999, "best_ask": None,
          "bids": {0.999: 500.0}}
    intents, why = decide_quotes(_rcfg(), _book("UPTOK", 0.52, 0.53), dn,
                                 Inventory(), 200.0, None)
    assert intents == []
    assert "hedge" in why and "DOWN" in why, why


def test_turning_the_hard_blocks_off_stands_the_cap_down():
    """$240 of naked cost against a $120 budget, with the blocks switched off.

    Isolates the BLOCK as the cause of the refusals above -- nothing else about
    the input moved, so the switch is the only explanation for the cap standing
    down. The heavy side still does not REST, because the switch governs
    `hard_block` and nothing else, exactly as the config documents: at 200% of
    the budget U3's ladder sizes it to zero independently. Turning a block off
    cannot conjure a sensible size out of a budget that is already spent.
    """
    cfg = _rcfg(max_naked_usd=120.0, enable_hard_blocks=False)
    inv = Inventory(up_shares=400.0, down_shares=0.0,
                    up_cost=240.0, down_cost=0.0)
    up_book = {"token_id": "UPTOK", "best_bid": 0.60, "best_ask": 0.61,
               "bids": {0.60: 500.0}, "asks": {0.61: 500.0}}
    dn_book = {"token_id": "DNTOK", "best_bid": 0.39, "best_ask": 0.40,
               "bids": {0.39: 500.0}, "asks": {0.40: 500.0}}
    assert risk.hard_block(cfg, inv, "UP", 0.58, up_book, dn_book) is None
    intents, why = _quote(cfg, up=(0.60, 0.61), dn=(0.39, 0.40), inv=inv)
    assert "DOWN" in {q.side for q in intents}, why
    assert "UP" not in {q.side for q in intents}, why


def test_unsetting_the_budget_quotes_both_sides_at_twice_the_old_limit():
    """The same $240 position with `max_naked_usd=0`: the whole dollar system
    unset, the way every other cap here is unset. Block, ladder and skew all
    stand down together, and both sides rest at full size."""
    inv = Inventory(up_shares=400.0, down_shares=0.0,
                    up_cost=240.0, down_cost=0.0)
    intents, why = _quote(_rcfg(max_naked_usd=0.0),
                          up=(0.60, 0.61), dn=(0.39, 0.40), inv=inv)
    assert {q.side for q in intents} == {"UP", "DOWN"}, why
    assert {q.size for q in intents} == {120}


def test_fleet_cap_stops_the_heavy_side_even_when_this_market_is_fine():
    """The per-market cap bounds ONE market; nothing bounded the fleet.

    Measured live: 16 markets each individually inside the 360-share cap still
    summed to $1,630 of unhedged exposure -- a +/-$456 swing against a $62
    edge. This market holds 100 shares carrying only $30 of naked cost -- a
    quarter of its own $120 cap, and low enough that U3's size ladder still
    rests 67 shares on the heavy side -- but the fleet is over budget, so it
    must stop adding.
    """
    inv = Inventory(up_shares=100.0, down_shares=0.0,
                    up_cost=30.0, down_cost=0.0)
    cfg = _rcfg(fleet_naked_usd=2000.0, max_fleet_naked_usd=800.0)
    intents, why = _quote(cfg, inv=inv)
    sides = {q.side for q in intents}
    assert "UP" not in sides, f"fleet over budget must stop the heavy side: {why}"
    assert "DOWN" in sides, f"light side still flattens: {why}"


def test_fleet_under_budget_quotes_both_sides_normally():
    """Same position, same books; only the fleet total moved. Isolates the
    fleet cap as what bound above -- and the heavy side comes back TAPERED, at
    67 of 120 shares, because $30 of $120 is a quarter of the budget."""
    inv = Inventory(up_shares=100.0, down_shares=0.0,
                    up_cost=30.0, down_cost=0.0)
    cfg = _rcfg(fleet_naked_usd=100.0, max_fleet_naked_usd=800.0)
    intents, why = _quote(cfg, inv=inv)
    assert {q.side for q in intents} == {"UP", "DOWN"}, why
    assert [q.size for q in intents if q.side == "UP"] == [67]
    assert [q.size for q in intents if q.side == "DOWN"] == [120]


def test_skew_never_leaves_the_reward_window_or_crosses():
    """Skew must not push a quote outside 4.5c -- outside it scores nothing.

    Two readings. A loop over the huge case alone would say nothing about the
    side skew pushes AWAY from mid, because U3's ladder removes it: at 100% of
    the budget the heavy side is sized to zero and only the light side is left
    to iterate over. So the window is pinned at a utilization where BOTH sides
    still rest, and the clamp at the near end is pinned separately.
    """
    # 70% of the budget, on a 1000-share base so the taper still leaves an
    # order (1000*(1-0.7)^2 = 90 shares) on the heavy side.
    cfg = _rcfg(quote_shares=1000, min_quote_shares=50, max_naked_usd=120.0)
    inv = Inventory(up_shares=84.0 / 0.52, down_shares=0.0,
                    up_cost=84.0, down_cost=0.0)
    intents, why = _quote(cfg, inv=inv)
    assert {q.side for q in intents} == {"UP", "DOWN"}, why
    for q in intents:
        s = q.mid - q.price
        assert cfg.min_reward_offset - 1e-9 <= s <= cfg.max_spread_from_mid + 1e-9
        assert q.price < q.mid

    # 400x the budget: utilization clamps at 1.0, so the light side lands
    # exactly on min_reward_offset (0.020 - 0.015) rather than walking onto the
    # touch, and the heavy side is gone on size rather than on skew.
    huge = Inventory(up_shares=100000.0, down_shares=0.0,
                     up_cost=50000.0, down_cost=0.0)
    base = _rcfg()
    intents, why = _quote(base, inv=huge)
    assert [q.side for q in intents] == ["DOWN"], why
    only = intents[0]
    assert abs((only.mid - only.price) - base.min_reward_offset) < 1e-9
    assert only.price < only.mid


# --- emergency stop-loss (taker exception) ----------------------------------
#
# The rewards objective otherwise never crosses: taker fee is 0.07*p*(1-p),
# 1.75c/share at p=0.50, larger than any edge in this book. These tests pin the
# one case where that arithmetic stops applying -- when the alternative is not
# a smaller profit but an unbounded naked leg in a market running away from us.

def _rw(**kw):
    kw.setdefault("objective", "rewards")
    kw.setdefault("max_naked_usd", 120.0)
    kw.setdefault("emergency_hedge_frac", 0.8)
    return dataclasses.replace(BASE, **kw)


def _lopsided(up_sh=400.0, dn_sh=0.0, up_px=0.52):
    """Long UP, nothing on DOWN: 400sh of deficit on the DOWN side.

    Valued at the heavy leg's 0.52 average that is $208 of hedge still to buy,
    past the $96 (0.8 x $120) emergency trigger. The trigger is stated in
    dollars for the same reason the cap it sits inside is: 400 shares is $80 of
    deficit at 0.20 and $340 at 0.85, and only one of those is an emergency.
    """
    return Inventory(up_shares=up_sh, down_shares=dn_sh, up_cost=up_sh * up_px,
                     down_cost=0.0)


def _losing_books():
    """UP mid 0.455, below the 0.52 we paid: the heavy leg is losing now."""
    return _book("UPTOK", 0.45, 0.46), _book("DNTOK", 0.53, 0.54)


def test_unhedged_and_losing_crosses_the_spread():
    up, dn = _losing_books()
    intents, why = decide_quotes(_rw(), up, dn, _lopsided(), 1e9, None)
    hedge = [q for q in intents if q.crossed]
    assert len(hedge) == 1, why
    assert hedge[0].side == "DOWN"
    assert hedge[0].price == 0.54            # the ask, not a tick under it
    assert hedge[0].size == 400              # the whole deficit


def test_a_big_deficit_in_a_flat_market_does_not_cross():
    """Size alone is not an emergency. UP mid 0.525 is above the 0.52 we paid,
    so the position is not losing and the fee would buy nothing."""
    up = _book("UPTOK", 0.52, 0.53)
    dn = _book("DNTOK", 0.46, 0.47)
    intents, _ = decide_quotes(_rw(), up, dn, _lopsided(), 1e9, None)
    assert [q for q in intents if q.crossed] == []


def test_a_losing_position_inside_the_trigger_does_not_cross():
    """150sh at a 0.52 average is $78 of deficit, under 0.8 x $120 = $96.

    Skew still owns this range, and paying the taker fee inside it buys nothing
    the resting light-side bid was not already going to get for free.
    """
    up, dn = _losing_books()
    intents, _ = decide_quotes(_rw(), up, dn, _lopsided(up_sh=150.0), 1e9, None)
    assert [q for q in intents if q.crossed] == []


def test_an_unhealthy_hedge_book_does_not_stop_the_emergency_cross():
    """R4, on the case that matters most. Both books here are 16c wide, which
    the book-health arm refuses for a NEW bid -- and this is not a new bid, it
    is the exit from a $208 naked leg whose mid (0.34) has fallen 18c below the
    0.52 we paid. A gate that refuses the hedge in the one market that needs it
    has inverted its own purpose.
    """
    up = _book("UPTOK", 0.26, 0.42)      # mid 0.34, far under the 0.52 average
    dn = _book("DNTOK", 0.58, 0.74)
    intents, why = decide_quotes(_rw(), up, dn, _lopsided(), 1e9, None)
    hedge = [q for q in intents if q.crossed]
    assert len(hedge) == 1, why
    assert hedge[0].side == "DOWN"
    assert hedge[0].price == 0.74        # the ask -- we are taking, not resting


def test_the_exception_is_switchable():
    """Isolates the exception as the cause -- nothing else about the input
    moved, and a taker order is the one thing this strategy otherwise never
    does, so it must be measurable on its own."""
    up, dn = _losing_books()
    intents, _ = decide_quotes(_rw(enable_emergency_hedge=False), up, dn,
                               _lopsided(), 1e9, None)
    assert [q for q in intents if q.crossed] == []


def test_ordinary_reward_quotes_are_never_marked_crossed():
    intents, _ = decide_quotes(_rw(), _book("UPTOK", 0.52, 0.53),
                               _book("DNTOK", 0.46, 0.47), Inventory(),
                               1e9, None)
    assert intents and all(not q.crossed for q in intents)
    assert all(q.price < q.mid for q in intents)


# --- U3: bounding what is committed, not just what is unhedged ---------------

def _rw_quote(cfg, inv=None):
    return decide_quotes(cfg, _book("UPTOK", 0.52, 0.53),
                         _book("DNTOK", 0.46, 0.47), inv or Inventory(),
                         1e9, None)


def test_under_the_committed_cap_both_sides_quote():
    intents, _ = _rw_quote(_rw(max_committed_usd=2000.0, committed_usd=500.0))
    assert {q.side for q in intents} == {"UP", "DOWN"}


def test_at_the_committed_cap_a_balanced_book_stops_quoting():
    """Balanced means neither side reduces anything, so both are additions."""
    intents, why = _rw_quote(
        _rw(max_committed_usd=2000.0, committed_usd=2000.0))
    assert intents == []
    assert "committed" in why


def test_at_the_committed_cap_the_reducing_side_still_quotes():
    """The cap must never remove the only route back under itself. Merge needs
    a matched pair, and the light side is what produces one -- blocking it
    would freeze the fleet at maximum commitment permanently."""
    inv = Inventory(up_shares=300.0, down_shares=0.0, up_cost=300.0 * 0.52,
                    down_cost=0.0)
    intents, _ = _rw_quote(
        _rw(max_committed_usd=2000.0, committed_usd=2500.0), inv=inv)
    assert [q.side for q in intents] == ["DOWN"]      # the light side only


def test_inventory_alone_can_breach_the_cap_with_no_offers_resting():
    """The cap is on committed capital, not on open orders. $9,588 had left
    the wallet while only $1,369 was resting in offers."""
    intents, why = _rw_quote(
        _rw(max_committed_usd=2000.0, committed_usd=9588.0))
    assert intents == []
    assert "committed" in why


def test_the_committed_cap_names_itself_separately_from_the_naked_cap():
    """An operator reading 'not adding' has to be able to tell which limit
    bound, or the dashboard shows a dead market with no explanation."""
    _, why = _rw_quote(_rw(max_committed_usd=2000.0, committed_usd=2000.0,
                           fleet_naked_usd=0.0))
    assert "committed" in why and "unhedged" not in why


def test_a_zero_committed_cap_disables_the_rule():
    """Same escape hatch every other cap here has -- 0 means unset, not
    'commit nothing'."""
    intents, _ = _rw_quote(_rw(max_committed_usd=0.0, committed_usd=99999.0))
    assert intents


# --- U3: the fill cap that never ran ----------------------------------------

def test_the_fill_cap_applies_to_the_rewards_objective():
    """REGRESSION. max_fills_per_market was checked in `decide_quotes` several
    lines AFTER the rewards path had already returned, so it never executed on
    the objective the fleet actually runs. Three markets reached 26 fills
    against a nominal limit of 25."""
    inv = Inventory(fills=25)
    intents, why = _rw_quote(_rw(max_fills_per_market=25), inv=inv)
    assert intents == []
    assert "25 fills" in why


def test_one_fill_below_the_cap_still_quotes():
    inv = Inventory(fills=24)
    intents, _ = _rw_quote(_rw(max_fills_per_market=25), inv=inv)
    assert intents


def test_a_zero_allocation_actually_stops_quoting():
    """REGRESSION. reallocate has always documented that an unfunded market
    'gets 0 and stops quoting', and it never did -- `max(quote_shares,
    min_quote_shares)` promoted the 0 back to the venue minimum, so a
    deliberately defunded market kept posting 50-share orders."""
    intents, why = _rw_quote(_rw(quote_shares=0))
    assert intents == []
    assert "unfunded" in why


# --- U3: the continuous size ladder -----------------------------------------
#
# The dollar cap of U2 is a cliff: $119.99 of naked cost rests the full 120
# shares and $120.00 rests nothing. That shape makes the LAST order before the
# limit the LARGEST one -- on lol-maz-mg1 the position took its biggest single
# step toward the cap at the moment it was closest to it. R5 replaces the step
# with base*(1-u)^2, so size decays as the budget fills and reaches zero AT it.


def _heavy_up(usd, avg=0.52):
    """UP-heavy inventory holding exactly `usd` of naked cost at `avg`."""
    return Inventory(up_shares=(usd / avg) if usd else 0.0, down_shares=0.0,
                     up_cost=usd, down_cost=0.0)


def test_size_walks_down_to_nothing_instead_of_stepping_off_a_cliff():
    """U3's Done signal: flat -> budget produces a DECREASING size sequence.

    400 shares of base, a $120 budget, and a market walked from flat to the cap
    in $24 steps. The old rule produced 400, 400, 400, 400, 400, none. The
    ladder lands softly -- the last order placed is 64 shares, 16% of full size,
    and the sequence never steps from full size to zero.
    """
    cfg = _rw(quote_shares=400, min_quote_shares=50, max_naked_usd=120.0)
    seq = []
    for usd in (0.0, 24.0, 48.0, 72.0, 96.0, 120.0):
        intents, _ = _rw_quote(cfg, inv=_heavy_up(usd))
        up = [q.size for q in intents if q.side == "UP"]
        seq.append(up[0] if up else None)

    assert seq == [400, 191, 144, 64, None, None]
    live = [s for s in seq if s is not None]
    assert live == sorted(live, reverse=True) and len(set(live)) == len(live)
    assert live[-1] < 400 * 0.25, "the last order before the cap must be small"


def test_the_light_side_keeps_full_size_all_the_way_up_the_ladder():
    """R4 in the sizing layer: the taper must never slow the exit. Same walk as
    above, reading the side that REDUCES exposure."""
    cfg = _rw(quote_shares=400, min_quote_shares=50, max_naked_usd=120.0)
    for usd in (24.0, 48.0, 72.0, 96.0, 120.0):
        intents, why = _rw_quote(cfg, inv=_heavy_up(usd))
        down = [q.size for q in intents if q.side == "DOWN"]
        assert down == [400], f"light side tapered at ${usd:.0f}: {why}"


def test_the_taper_names_itself_when_it_zeroes_a_side():
    """A zero-size order is not an order: no intent, and a reason of its own.

    `_decide_quotes_rewards` returns an empty reason whenever ANY side quotes,
    so the log is only readable on a market where nothing rests. DOWN here has
    no ask to quote against, and the hard blocks are off, which leaves the
    ladder as the only rule that can refuse UP -- and it must not borrow the
    dollar cap's wording, or an operator would go looking for the wrong limit.
    """
    cfg = _rw(max_naked_usd=120.0, enable_hard_blocks=False)
    dn = {"token_id": "DNTOK", "best_bid": 0.46, "best_ask": None,
          "bids": {0.46: 500.0}}
    intents, why = decide_quotes(cfg, _book("UPTOK", 0.52, 0.53), dn,
                                 _heavy_up(240.0), 1e9, None)
    assert intents == []
    assert "tapered to 0" in why, why
    assert "not adding" not in why, why


def test_no_side_ever_rests_a_zero_size_order():
    """The whole walk, read for the property a zero would violate: an order
    below rewardsMinSize scores nothing and still buys inventory."""
    cfg = _rw(quote_shares=400, min_quote_shares=50, max_naked_usd=120.0)
    for usd in (0.0, 24.0, 48.0, 72.0, 96.0, 120.0):
        intents, _ = _rw_quote(cfg, inv=_heavy_up(usd))
        assert all(q.size >= cfg.min_quote_shares for q in intents)


def test_well_inside_the_budget_the_heavy_side_still_rests_at_a_reduced_size():
    """Isolates the ladder against 'the heavy side just stopped quoting'.

    $36 of $120 is 30% utilization: 120*(1-0.3)^2 = 58.8 shares, which clears
    the 50-share reward minimum. The side keeps scoring, at half its size.
    """
    intents, why = _rw_quote(_rw(max_naked_usd=120.0), inv=_heavy_up(36.0))
    up = [q.size for q in intents if q.side == "UP"]
    assert up == [58], why
    assert up[0] < 120, "size must respond to utilization at all"


def test_a_zero_budget_leaves_every_size_full():
    """0 unsets the dollar machinery wholesale, the same escape hatch every
    other cap here has -- not 'taper everything to nothing'."""
    inv = Inventory(up_shares=400.0, down_shares=0.0, up_cost=240.0)
    intents, why = _rw_quote(_rw(max_naked_usd=0.0), inv=inv)
    assert {q.side for q in intents} == {"UP", "DOWN"}, why
    assert {q.size for q in intents} == {120}


def test_the_skew_responds_to_dollars_not_to_share_count():
    """THE UNIT, read end to end. The SAME 100-share imbalance in a 0.34 market
    and a 0.06 market: $34 of downside against $6, and the expensive one is the
    urgent one. The share-denominated spring answered both identically, which
    is why it was still ramping on lol-maz-mg1 at 233 of 240 shares while the
    position was already fully built.
    """
    cfg = _rw(max_naked_usd=120.0)
    dear = Inventory(up_shares=100.0, down_shares=0.0, up_cost=34.0)
    cheap = Inventory(up_shares=100.0, down_shares=0.0, up_cost=6.0)
    assert dear.up_shares == cheap.up_shares, "same SHARE imbalance by design"

    a, why_a = decide_quotes(cfg, _book("UPTOK", 0.33, 0.35),
                             _book("DNTOK", 0.65, 0.67), dear, 1e9, None)
    b, why_b = decide_quotes(cfg, _book("UPTOK", 0.05, 0.07),
                             _book("DNTOK", 0.93, 0.95), cheap, 1e9, None)
    off_a = {q.side: q.mid - q.price for q in a}
    off_b = {q.side: q.mid - q.price for q in b}
    assert "UP" in off_a and "UP" in off_b, f"{why_a} / {why_b}"
    assert off_a["UP"] > off_b["UP"], (
        "the more expensive naked position must be pushed further from mid")
    assert off_a["DOWN"] < off_b["DOWN"], (
        "and its hedge must be pulled harder toward mid")

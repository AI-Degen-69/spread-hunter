"""Unit tests for live/engine/quotes.py, risk.py, gate.py and inventory rebuilding."""
import sqlite3
import pytest
from core_brain.config import MakerConfig
from core_brain.order_registry import OrderRecord, OrderRegistry, FillRecord, inventory_from_registry
from core_brain.quotes import Inventory, QuoteIntent, decide_quotes, mid_price
from core_brain import risk, unhedged_stop_loss


def test_mid_price_calculation():
    assert mid_price(0.40, 0.42) == pytest.approx(0.41)
    assert mid_price(None, 0.42) is None
    assert mid_price(0.40, None) is None


def test_decide_quotes_basic_two_sided():
    # Fixed-share sizing: this test is about the port's quoting behaviour, not
    # about the pilot's dollar allocation, so it names the mode rather than
    # inheriting whichever one is currently the default.
    cfg = MakerConfig(
        objective="rewards",
        size_mode="shares",
        quote_shares=120,
        min_quote_shares=50,
        reward_offset=0.02,
        price_band_low=0.10,
        price_band_high=0.90,
    )
    up_book = {
        "best_bid": 0.50, "best_ask": 0.52,
        "bids": {0.50: 1000.0}, "asks": {0.52: 1000.0}
    }
    down_book = {
        "best_bid": 0.48, "best_ask": 0.50,
        "bids": {0.48: 1000.0}, "asks": {0.50: 1000.0}
    }
    inv = Inventory()
    intents, why = decide_quotes(cfg, up_book, down_book, inv, 1e9, None)

    assert len(intents) == 2
    assert not why
    up_intent = [i for i in intents if i.side == "UP"][0]
    down_intent = [i for i in intents if i.side == "DOWN"][0]

    assert up_intent.price < 0.51
    assert down_intent.price < 0.49
    assert up_intent.size >= 50
    assert down_intent.size >= 50


def test_decide_quotes_outside_band_or_settled_declined():
    cfg = MakerConfig(
        objective="rewards",
        quote_shares=120,
        min_quote_shares=50,
        price_band_low=0.10,
        price_band_high=0.90,
    )
    # Severe one-sided market outside band / settled
    up_book = {
        "best_bid": 0.98, "best_ask": 0.99,
        "bids": {0.98: 1000.0}, "asks": {0.99: 1000.0}
    }
    down_book = {
        "best_bid": 0.01, "best_ask": 0.02,
        "bids": {0.01: 1000.0}, "asks": {0.02: 1000.0}
    }
    inv = Inventory()
    intents, why = decide_quotes(cfg, up_book, down_book, inv, 1e9, None)
    assert len(intents) == 0
    assert "settled" in why or "outside band" in why or "not tradeable" in why or "decided market" in why or "outside [0.20,0.80]" in why


def test_inventory_from_registry(tmp_path):
    db_path = tmp_path / "test_live.db"
    reg = OrderRegistry(db_path=db_path)

    cid = "0x" + "1" * 64
    up_token = "tok_up"
    down_token = "tok_down"

    # Insert orders
    o1 = OrderRecord(
        id="ord_1", order_id="venue_1", condition_id=cid, token_id=up_token,
        side="BUY", price=0.45, original_size=100.0, status="open",
        posted_ts=1000, last_polled_ts=1000, pair_id="pair_1"
    )
    o2 = OrderRecord(
        id="ord_2", order_id="venue_2", condition_id=cid, token_id=down_token,
        side="BUY", price=0.48, original_size=100.0, status="open",
        posted_ts=1000, last_polled_ts=1000, pair_id="pair_1"
    )
    reg.create_order(o1)
    reg.create_order(o2)

    # Record fills
    reg.record_fill(FillRecord(trade_id="t1", order_uuid="ord_1", size=60.0, price=0.45, venue_ts=1050000))
    reg.record_fill(FillRecord(trade_id="t2", order_uuid="ord_2", size=40.0, price=0.48, venue_ts=1060000))

    inv = inventory_from_registry(cid, up_token, down_token, db_path=db_path)
    assert inv.up_shares == 60.0
    assert inv.down_shares == 40.0
    assert inv.up_cost == pytest.approx(60.0 * 0.45)
    assert inv.down_cost == pytest.approx(40.0 * 0.48)
    assert inv.fills == 2
    assert inv.last_fill_ts == pytest.approx(1060.0)
    assert inv.balance == pytest.approx(40.0 / 60.0)


def test_decide_quotes_dollars_mode_characterisation():
    """Pin the behaviour of dollars mode under frozen book and config."""
    # 1. Normal couple sizing with taper waived at coin-flip
    cfg_waived = MakerConfig(
        objective="rewards",
        size_mode="dollars",
        bankroll_usd=100.0,
        couple_risk_frac=0.01,
        min_couple_usd=6.0,
        min_quote_shares=5,
        quote_shares=120,
        reward_offset=0.02,
        waive_attenuation_below_floor=True,
    )
    up_book = {
        "best_bid": 0.51, "best_ask": 0.52,
        "bids": {0.51: 1000.0}, "asks": {0.52: 1000.0}
    }
    down_book = {
        "best_bid": 0.48, "best_ask": 0.49,
        "bids": {0.48: 1000.0}, "asks": {0.49: 1000.0}
    }
    inv = Inventory()
    intents, why = decide_quotes(cfg_waived, up_book, down_book, inv, 1e9, None)

    assert len(intents) == 2
    assert not why
    up_qi = [i for i in intents if i.side == "UP"][0]
    dn_qi = [i for i in intents if i.side == "DOWN"][0]

    assert up_qi.price == 0.480
    assert dn_qi.price == 0.452
    assert up_qi.size == 5
    assert dn_qi.size == 5
    assert "price-risk taper x0.46 waived: 2sh is under the 5sh venue floor" in up_qi.reason
    assert "price-risk taper x0.55 waived: 3sh is under the 5sh venue floor" in dn_qi.reason

    # 2. Allocation cannot clear the venue floor
    cfg_shortfall = MakerConfig(
        objective="rewards",
        size_mode="dollars",
        bankroll_usd=10.0,
        couple_risk_frac=0.01,
        min_couple_usd=2.0,
        min_quote_shares=5,
        quote_shares=120,
        reward_offset=0.02,
    )
    intents_short, why_short = decide_quotes(cfg_shortfall, up_book, down_book, inv, 1e9, None)
    assert len(intents_short) == 0
    assert "$2.00 couple allocation at pair price 0.932 buys 2sh, below the venue minimum of 5sh" in why_short
    assert "needs $4.66, shortfall $2.66" in why_short


def test_decide_quotes_dollars_mode_defunded():
    """quote_shares == 0 in dollars mode means defunded: quote nothing."""
    cfg_defunded = MakerConfig(
        objective="rewards",
        size_mode="dollars",
        quote_shares=0,
        bankroll_usd=100.0,
        min_couple_usd=6.0,
        min_quote_shares=5,
    )
    up_book = {
        "best_bid": 0.50, "best_ask": 0.52,
        "bids": {0.50: 1000.0}, "asks": {0.52: 1000.0}
    }
    down_book = {
        "best_bid": 0.48, "best_ask": 0.50,
        "bids": {0.48: 1000.0}, "asks": {0.50: 1000.0}
    }
    inv = Inventory()
    intents, why = decide_quotes(cfg_defunded, up_book, down_book, inv, 1e9, None)
    assert len(intents) == 0
    assert why == "unfunded by the allocator -- quoting nothing"


def test_flat_inventory_refuses_a_lone_leg():
    """One resting leg against no inventory is a naked position by construction.

    If it fills, the hedge does not exist and the only way to get it is to
    cross, paying away the spread the quote was resting to earn. The couple is
    the product; half a couple is a directional bet nobody chose.
    """
    from dataclasses import replace
    from core_brain.config import load

    cfg = replace(load(), bankroll_usd=5000.0, price_band_low=0.30, price_band_high=0.70)
    flat = Inventory(up_shares=0, down_shares=0, up_cost=0.0, down_cost=0.0)
    # UP is blocked by price band (mid 0.25 -> price ~0.23 < 0.30) while
    # DOWN (mid 0.65 -> price ~0.63) passes. Both mids inside [0.20,0.80]
    # so the 2026-08-28 tighten does not mask this lone-leg case.
    up = {"best_bid": 0.24, "best_ask": 0.26,
          "bids": {0.24: 9999.0}, "asks": {0.26: 9999.0}}
    down = {"best_bid": 0.64, "best_ask": 0.66,
            "bids": {0.64: 9999.0}, "asks": {0.66: 9999.0}}

    intents, why = decide_quotes(cfg, up, down, flat, 1e9, None)
    assert intents == []
    assert "lone resting leg is a naked position" in why

    # Unbalanced is the opposite case: the single intent is the LIGHT side and
    # it flattens the position, so it must still be allowed through.
    naked = Inventory(up_shares=40, down_shares=0, up_cost=4.0, down_cost=0.0)
    intents, why = decide_quotes(cfg, up, down, naked, 1e9, None)
    assert [i.side for i in intents] == ["DOWN"]
    assert why == ""


def test_flat_pair_sizes_harmonize_to_minimum():
    """Skewed books taper each leg differently; the resting pair must be symmetric.

    UP near the coin flip is cut harder than DOWN further out, so the two legs
    come out of sizing at different share counts. A pair that fills asymmetric
    leaves a naked surplus that cannot merge, so both legs clamp to the min.
    """
    cfg = MakerConfig(
        objective="rewards",
        size_mode="shares",
        quote_shares=120,
        min_quote_shares=5,
        reward_offset=0.02,
        price_band_low=0.10,
        price_band_high=0.90,
    )
    up_book = {
        "best_bid": 0.54, "best_ask": 0.56,
        "bids": {0.54: 1000.0}, "asks": {0.56: 1000.0}
    }
    down_book = {
        "best_bid": 0.44, "best_ask": 0.46,
        "bids": {0.44: 1000.0}, "asks": {0.46: 1000.0}
    }
    inv = Inventory()
    intents, why = decide_quotes(cfg, up_book, down_book, inv, 1e9, None)

    assert len(intents) == 2
    assert not why
    up_qi = [i for i in intents if i.side == "UP"][0]
    dn_qi = [i for i in intents if i.side == "DOWN"][0]
    assert up_qi.size == dn_qi.size
    assert "clamped" in up_qi.reason + dn_qi.reason


def test_harmonized_pair_below_floor_drops_both():
    """A pair whose common size cannot clear the venue floor posts nothing.

    Half a pair is a directional bet nobody chose, so both legs are refused
    with an informative reason rather than resting a single leg.
    """
    from core_brain.quotes import _require_two_sided
    cfg = MakerConfig(min_quote_shares=5)
    intents = [
        QuoteIntent(side="UP", token_id="tok_up", price=0.50, size=4,
                    mid=0.52, edge_vs_mid=0.02),
        QuoteIntent(side="DOWN", token_id="tok_dn", price=0.43, size=3,
                    mid=0.45, edge_vs_mid=0.02),
    ]
    out, why = _require_two_sided(cfg, Inventory(), intents, "")
    assert out == []
    assert "3sh" in why and "5sh" in why

"""Unit tests for live/engine/quotes.py, risk.py, gate.py and inventory rebuilding."""
import sqlite3
import pytest
from engine.config import MakerConfig
from engine.order_registry import OrderRecord, OrderRegistry, FillRecord, inventory_from_registry
from engine.quotes import Inventory, QuoteIntent, decide_quotes, mid_price
from engine import gate, risk


def test_mid_price_calculation():
    assert mid_price(0.40, 0.42) == pytest.approx(0.41)
    assert mid_price(None, 0.42) is None
    assert mid_price(0.40, None) is None


def test_decide_quotes_basic_two_sided():
    cfg = MakerConfig(
        objective="rewards",
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
    assert "settled" in why or "outside band" in why or "not tradeable" in why


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

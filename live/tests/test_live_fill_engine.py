"""Unit tests for LiveFillEngine interface compliance, mock client execution, and invariant guards."""
import pytest
from unittest.mock import MagicMock
from engine.live_fill_engine import LiveFillEngine, LiveRestingOrder, LiveFill
from engine.order_registry import OrderRegistry


@pytest.fixture
def mock_registry(tmp_path):
    return OrderRegistry(db_path=tmp_path / "test_engine.db")


@pytest.fixture
def mock_client():
    client = MagicMock()
    return client


def test_live_fill_engine_post_amend_cancel(mock_registry, mock_client):
    engine = LiveFillEngine(registry=mock_registry, client=mock_client, condition_id="0x123")
    bids = {0.45: 500.0, 0.44: 1000.0}

    # 1. Post
    order = engine.post(token_id="tok_up", side="UP", price=0.45, size=10.0, book_bids=bids, ts=100.0)
    assert order.token_id == "tok_up"
    assert order.side == "UP"
    assert order.price == 0.45
    assert order.size == 10.0
    assert order.queue_ahead == 500.0
    assert order.is_open
    assert len(engine.open_orders()) == 1
    assert len(engine.open_orders("tok_up")) == 1
    assert len(engine.open_orders("tok_down")) == 0

    # 2. Amend
    bids[0.46] = 200.0
    amended = engine.amend(order, price=0.46, book_bids=bids, ts=105.0)
    assert amended.price == 0.46
    assert amended.queue_ahead == 200.0
    assert amended.posted_ts == 105.0

    # 3. Cancel
    cancelled_count = engine.cancel(token_id="tok_up", ts=110.0, reason="requote")
    assert cancelled_count == 1
    assert not order.is_open
    assert order.cancelled
    assert order.cancelled_ts == 110.0
    assert order.cancel_reason == "requote"
    assert len(engine.open_orders()) == 0


def test_live_fill_engine_on_book_never_infers_fills(mock_registry, mock_client):
    """CRITICAL INVARIANT TEST: on_book MUST NEVER invent synthetic fills from book changes or tape."""
    engine = LiveFillEngine(registry=mock_registry, client=mock_client, condition_id="0x123")
    order = engine.post(token_id="tok_up", side="UP", price=0.50, size=20.0, book_bids={0.50: 100.0}, ts=100.0)

    # Book completely cleared and trades occurred at price
    # In simulation (QueueFillEngine), this would generate a sweep/queue fill.
    # In LiveFillEngine, on_book MUST return 0 fills.
    bids_after = {0.48: 50.0}
    tape = {0.50: 500.0}
    fills = engine.on_book(token_id="tok_up", bids=bids_after, ts=110.0, traded=tape)

    assert len(fills) == 0
    assert order.filled == 0.0
    assert order.remaining == 20.0
    assert engine.filled_shares() == 0.0
    assert engine.cost() == 0.0
    assert len(engine.unverified) == 0
    assert len(engine.reconciliation) == 0


def test_live_fill_engine_cross(mock_registry, mock_client):
    engine = LiveFillEngine(registry=mock_registry, client=mock_client, condition_id="0x123")
    asks = {0.52: 10.0, 0.53: 20.0}

    # Cross 15 shares against asks
    fills = engine.cross(token_id="tok_down", side="DOWN", size=15.0, book_asks=asks, ts=200.0, max_price=0.55)
    assert len(fills) == 2
    assert fills[0].price == 0.52
    assert fills[0].size == 10.0
    assert fills[0].reason == "cross"
    assert fills[1].price == 0.53
    assert fills[1].size == 5.0

    assert engine.filled_shares() == 15.0
    assert engine.filled_shares(include_crossed=False) == 0.0
    assert engine.filled_shares(side="DOWN") == 15.0
    assert engine.cost(side="DOWN") == pytest.approx(10 * 0.52 + 5 * 0.53)
    assert engine.avg_price(side="DOWN") == pytest.approx((10 * 0.52 + 5 * 0.53) / 15.0)


def test_live_fill_engine_record_venue_fill(mock_registry, mock_client):
    engine = LiveFillEngine(registry=mock_registry, client=mock_client, condition_id="0x123")
    order = engine.post(token_id="tok_up", side="UP", price=0.45, size=10.0, book_bids={0.45: 50.0}, ts=100.0)

    # Record verified venue fill
    fill = engine.record_venue_fill(trade_id="tr_1", token_id="tok_up", side="UP", price=0.45, size=6.0, ts=120.0, order_id="venue_ord_1")
    assert fill.size == 6.0
    assert fill.price == 0.45
    assert fill.reason == "venue"
    assert order.filled == 6.0
    assert order.remaining == 4.0
    assert order.is_open

    assert engine.filled_shares() == 6.0
    assert engine.filled_shares(include_crossed=False) == 6.0
    assert engine.cost() == pytest.approx(6.0 * 0.45)
    assert engine.avg_price() == pytest.approx(0.45)


def test_record_venue_fill_splits_one_fill_across_two_orders(mock_registry, mock_client):
    """A 6-share fill must not become 12 shares across two resting orders.

    The old loop recomputed `min(o.remaining, size)` against the full fill size
    for every match and broke only when an order was fully consumed, so it
    attributed the whole fill to each order in turn. The single-order case was
    correct, which is why the existing test passed.
    """
    engine = LiveFillEngine(registry=mock_registry, client=mock_client, condition_id="0x123")
    a = engine.post(token_id="tok_up", side="UP", price=0.45, size=10.0, book_bids={}, ts=100.0)
    b = engine.post(token_id="tok_up", side="UP", price=0.45, size=10.0, book_bids={}, ts=101.0)

    engine.record_venue_fill(trade_id="tr_1", token_id="tok_up", side="UP",
                             price=0.45, size=6.0, ts=120.0)

    assert a.filled + b.filled == pytest.approx(6.0)
    assert a.filled == pytest.approx(6.0)
    assert b.filled == pytest.approx(0.0)
    assert a.is_open and b.is_open


def test_record_venue_fill_overflows_into_the_next_order(mock_registry, mock_client):
    """A fill larger than the first order spills into the second, never beyond the total."""
    engine = LiveFillEngine(registry=mock_registry, client=mock_client, condition_id="0x123")
    a = engine.post(token_id="tok_up", side="UP", price=0.45, size=4.0, book_bids={}, ts=100.0)
    b = engine.post(token_id="tok_up", side="UP", price=0.45, size=10.0, book_bids={}, ts=101.0)

    engine.record_venue_fill(trade_id="tr_2", token_id="tok_up", side="UP",
                             price=0.45, size=9.0, ts=120.0)

    assert a.filled == pytest.approx(4.0)
    assert b.filled == pytest.approx(5.0)
    assert not a.is_open
    assert b.is_open


def test_record_venue_fill_prefers_an_exact_order_id(mock_registry, mock_client):
    """When the venue names the order, attribute to that one and not to its neighbour."""
    engine = LiveFillEngine(registry=mock_registry, client=mock_client, condition_id="0x123")
    a = engine.post(token_id="tok_up", side="UP", price=0.45, size=10.0, book_bids={}, ts=100.0)
    b = engine.post(token_id="tok_up", side="UP", price=0.45, size=10.0, book_bids={}, ts=101.0)
    b.order_id = "venue_ord_b"

    engine.record_venue_fill(trade_id="tr_3", token_id="tok_up", side="UP",
                             price=0.45, size=3.0, ts=120.0, order_id="venue_ord_b")

    assert a.filled == pytest.approx(0.0)
    assert b.filled == pytest.approx(3.0)

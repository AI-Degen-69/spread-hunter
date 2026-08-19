"""End-to-end live lifecycle: decide -> submit -> reconcile, real code paths.

This is the closest thing to the real-money smoke test that can run inside the
hermetic test suite (conftest blocks non-loopback sockets). Every layer except
the venue itself is real:

  * a real `OrderRegistry` on a real SQLite file,
  * the real `engine.quotes.decide_quotes` (reward objective + every risk gate),
  * the real `engine.live_fleet._submit_intents` (row-first placement path),
  * the real `engine.order_registry.reconcile_orders` (fill adoption + status).

Only `FakeVenue` stands in for Polymarket's CLOB, and it records the row state
at the exact moment `post_orders` is called, so the test can assert the
row-first discipline too (a `pending` row must exist with no venue id before
the venue is told about the order).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from engine.config import load
from engine.live_fleet import _submit_intents
from engine.order_registry import OrderRegistry, reconcile_orders
from engine.quotes import Inventory, decide_quotes


class FakeVenue:
    """Deterministic CLOB stand-in for the submit + reconcile surface."""

    def __init__(self, registry: OrderRegistry | None = None) -> None:
        self.creds = {"key": "test"}          # non-None: reconcile may run
        self.registry = registry
        self.open_orders: list[dict] = []     # venue resting orders
        self.trades: list[dict] = []          # venue trade tape
        self._next_id = 0
        self.pending_at_post: list[tuple] = []  # (id, order_id, status) mid-post

    def get_open_orders(self, params=None, only_first_page=False, next_cursor=None):
        return list(self.open_orders)

    def get_trades(self, params=None, only_first_page=False, next_cursor=None):
        return list(self.trades)

    def create_order(self, order_args):
        # The signed order is opaque to the test; the venue id is minted in
        # post_orders.
        return {"signed": True}

    def post_orders(self, batch_args, post_only=True):
        # Row-first discipline: by the time the venue sees the order, the
        # registry already holds a 'pending' row with no venue id.
        if self.registry is not None:
            self.pending_at_post = [
                (o.id, o.order_id, o.status)
                for o in self.registry.get_active_orders()
                if o.status == "pending"
            ]
        resp = []
        for _ in batch_args:
            self._next_id += 1
            resp.append({"orderID": f"0xvenue{self._next_id}"})
        return resp


class Market:
    condition_id = "0xcond-e2e"
    market_slug = "e2e-lifecycle-market"


@pytest.fixture
def registry(tmp_path) -> OrderRegistry:
    return OrderRegistry(tmp_path / "lifecycle.db")


@pytest.fixture
def venue(registry) -> FakeVenue:
    return FakeVenue(registry)


def _book(token_id: str, best_bid: float, best_ask: float, depth: float = 500.0) -> dict:
    return {
        "token_id": token_id,
        "bids": {best_bid: depth},
        "asks": {best_ask: depth},
        "best_bid": best_bid,
        "best_ask": best_ask,
        "malformed": 0,
    }


def _decide(cfg):
    up = _book("tok-up", 0.73, 0.74)
    down = _book("tok-dn", 0.26, 0.27)
    return decide_quotes(cfg, up, down, Inventory(), 1e9, None)


def test_full_cycle_pending_to_open_to_filled(registry, venue):
    cfg = load()
    intents, why = _decide(cfg)

    # decide: both legs quoted, sized at the venue floor or above, tiny.
    assert len(intents) == 2, why
    assert {i.side for i in intents} == {"UP", "DOWN"}
    assert all(i.size >= cfg.min_quote_shares for i in intents)

    # submit: real row-first placement path.
    placed = _submit_intents(venue, registry, Market(), intents, cfg)
    assert placed == 2

    # Row-first: inside post_orders the rows were pending with no venue id.
    assert len(venue.pending_at_post) == 2
    assert all(order_id is None and status == "pending"
               for _, order_id, status in venue.pending_at_post)

    # After submit: both rows open with venue ids, and both quotes logged.
    orders = registry.get_active_orders()
    assert len(orders) == 2
    assert all(o.status == "open" and o.order_id for o in orders)
    assert len(registry.get_all_quotes()) == 2

    up = next(o for o in orders if o.token_id == "tok-up")
    down = next(o for o in orders if o.token_id == "tok-dn")

    # The venue now reports UP filled (a trade), and only DOWN still resting.
    venue.trades = [{
        "id": "trade-1",
        "taker_order_id": up.order_id,
        "size": up.original_size,
        "price": up.price,
        "match_time": 1_787_156_400_000,
    }]
    venue.open_orders = [{
        "id": down.order_id, "asset_id": down.token_id, "side": "BUY",
        "price": str(down.price), "original_size": str(down.original_size),
    }]

    summary = reconcile_orders(venue, registry, maker_address="0xmaker")

    assert summary.open_orders_count == 1
    assert summary.fills_recorded == 1
    assert summary.orders_filled == 1
    assert summary.orphans_adopted == 0
    assert summary.unattributed_recorded == 0

    # The filled leg closed; the untouched leg is still resting.
    assert registry.get_order(up.id).status == "filled"
    assert registry.get_order(down.id).status == "open"
    fills = registry.get_all_fills()
    assert len(fills) == 1
    assert fills[0]["order_uuid"] == up.id
    # A markout row opens on the fill (contaminated ref mid, as live records).
    assert len(registry.get_all_markouts()) == 1


def test_partial_fill_sets_partial_not_filled(registry, venue):
    cfg = load()
    intents, _ = _decide(cfg)
    _submit_intents(venue, registry, Market(), intents, cfg)

    up = next(o for o in registry.get_active_orders() if o.token_id == "tok-up")
    down = next(o for o in registry.get_active_orders() if o.token_id == "tok-dn")

    venue.trades = [{
        "id": "trade-half",
        "taker_order_id": up.order_id,
        "size": up.original_size / 2.0,
        "price": up.price,
        "match_time": 1_787_156_400_000,
    }]
    venue.open_orders = [{
        "id": up.order_id, "asset_id": up.token_id, "side": "BUY",
        "price": str(up.price), "original_size": str(up.original_size),
    }, {
        "id": down.order_id, "asset_id": down.token_id, "side": "BUY",
        "price": str(down.price), "original_size": str(down.original_size),
    }]

    summary = reconcile_orders(venue, registry, maker_address="0xmaker")

    assert summary.orders_partially_filled == 1
    assert summary.orders_filled == 0
    assert registry.get_order(up.id).status == "partial"
    assert registry.get_order(down.id).status == "open"

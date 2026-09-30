"""Refill-after-exit trap (issue #326): repro spike, no fix yet.

`load_pair` sizes exits off fills only; prior `single_buy_exit` closes net
solely on the venue side. Re-posting exited shares under the SAME pair_id
makes the next exit size fills-only naked against a netted venue position,
so `_check_positions` refuses as a would-be oversell -- and the refilled leg
strands, every rotation the same way. No oversell occurs: these tests pin the
refusal (the guard working) alongside the stranding (the bug).

The fake venue mirrors `tests/test_single_buy_saver.py` (same response
shapes, same quote-ledger side resolution). No network in any test.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from core_brain.order_registry import (
    OrderRegistry, OrderRecord, FillRecord, QuoteRecord,
)
from core_brain import single_buy_saver as lp

MAX_PAIR_COST = 0.995
TOK_UP = "tok-up"
TOK_DN = "tok-dn"
COND = "0xcond-refill"
PAIR = "pair-refill"
NOW = 2_000_000


@pytest.fixture
def registry(tmp_path):
    return OrderRegistry(db_path=tmp_path / "refill.db")


class FakeVenue:
    """Records venue calls. No network."""

    def __init__(self, best_ask=0.40, best_bid=0.55, depth=100.0):
        self.best_ask = best_ask
        self.best_bid = best_bid
        self.depth = depth
        self.calls: list[str] = []

    def get_order_book(self, token_id):
        return {
            "asset_id": token_id, "tick_size": "0.01",
            "asks": [{"price": str(self.best_ask), "size": str(self.depth)}],
            "bids": [{"price": str(self.best_bid), "size": str(self.depth)}],
        }

    def get_order(self, order_id):
        return {"orderID": order_id, "size_matched": 0.0}

    def cancel_order(self, payload):
        self.calls.append(f"cancel:{getattr(payload, 'orderID', payload)}")
        return {"canceled": True}

    def create_and_post_market_order(self, order_args, options=None,
                                     order_type="FOK", defer_exec=False):
        verb = "sell" if order_args.side == "SELL" else "buy"
        self.calls.append(f"{verb}:{order_args.token_id}:{order_args.amount}")
        return {"success": True, "orderID": f"venue-{verb}"}


def _resting_up(registry: OrderRegistry, size: float, price: float,
                when: int, tag: str) -> OrderRecord:
    order = OrderRecord(
        id=str(uuid.uuid4()), order_id=f"venue-up-{tag}", condition_id=COND,
        token_id=TOK_UP, side="BUY", price=price, original_size=size,
        status="open", posted_ts=when, last_polled_ts=when, pair_id=PAIR,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(order)
    return order


def _fill(registry: OrderRegistry, order: OrderRecord, size: float,
          price: float, when: int) -> None:
    registry.record_fill(FillRecord(
        trade_id=f"trade-{order.id}", order_uuid=order.id, size=size,
        price=price, venue_ts=when,
    ))
    registry.update_order_status(order.id, status="filled",
                                 last_polled_ts=when)


def _ladder_market(registry: OrderRegistry, when: int) -> None:
    """One DOWN rung resting plus the quotes ledger both legs need."""
    down = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-dn", condition_id=COND,
        token_id=TOK_DN, side="BUY", price=0.38, original_size=10.0,
        status="open", posted_ts=when, last_polled_ts=when, pair_id=PAIR,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(down)
    registry.log_quote(QuoteRecord(
        ts=when / 1000.0, condition_id=COND, token_id=TOK_UP, side="UP",
        price=0.60, size=10.0,
    ))
    registry.log_quote(QuoteRecord(
        ts=when / 1000.0, condition_id=COND, token_id=TOK_DN, side="DOWN",
        price=0.38, size=10.0,
    ))


def _cfg(grace_sec: float):
    return SimpleNamespace(
        single_buy_grace_sec=grace_sec, pairs_exit_window_sec=900.0,
        single_buy_max_loss_pct=0.10, single_buy_max_loss_usd=0.045,
        max_pair_cost=MAX_PAIR_COST, max_order_usd=100.0,
    )


def test_refill_after_exit_refuses_instead_of_overselling(registry):
    """Exit 10, refill 10 under the same stamp: the second exit refuses."""
    _ladder_market(registry, NOW)
    venue = FakeVenue(best_ask=0.40, best_bid=0.55)

    first = _resting_up(registry, 10.0, 0.60, NOW, "r1")
    _fill(registry, first, 10.0, 0.60, NOW)
    result = lp.exit_single_buy(venue, registry, PAIR,
                                max_pair_cost=MAX_PAIR_COST, live=True,
                                venue_positions={TOK_UP: 10.0, TOK_DN: 0.0})
    assert result["action"] == "exited"
    assert result["size"] == pytest.approx(10.0)

    # The venue sold those 10: it now holds 10 of the 20 fills on record.
    second = _resting_up(registry, 10.0, 0.60, NOW + 1, "r2")
    _fill(registry, second, 10.0, 0.60, NOW + 1)

    with pytest.raises(lp.PairExitRefused, match="diverge"):
        lp.exit_single_buy(venue, registry, PAIR,
                           max_pair_cost=MAX_PAIR_COST, live=True,
                           venue_positions={TOK_UP: 10.0, TOK_DN: 0.0})

    sells = [c for c in venue.calls if c.startswith("sell:")]
    assert len(sells) == 1, "the guard held: exactly one sale went out"
    closes = [c for c in registry.get_all_closes()
              if c.get("method") == "single_buy_exit"]
    assert len(closes) == 1, "the refilled 10 shares strand with no close"


@pytest.mark.parametrize("grace_sec", [0.0, 45.0])
def test_route_pair_reaches_the_same_refusal_under_both_graces(
        registry, grace_sec: float):
    """Old fills route past grace in both regimes, then hit the same wall."""
    # Timestamps are ms throughout: fills 200 s and 100 s old at route time.
    _ladder_market(registry, NOW - 200_000)
    # Bid 0.57 avoids adverse drift so the grace path itself decides.
    venue = FakeVenue(best_ask=0.40, best_bid=0.57)
    cfg = _cfg(grace_sec)

    first = _resting_up(registry, 10.0, 0.60, NOW - 200_000, "r1")
    _fill(registry, first, 10.0, 0.60, NOW - 200_000)
    routed = lp._route_pair(venue, registry, lp.load_pair(registry, PAIR),
                            MAX_PAIR_COST, True, {TOK_UP: 10.0, TOK_DN: 0.0},
                            cfg=cfg, last_ms=NOW - 200_000,
                            now_s=NOW / 1000.0)
    assert routed["action"] == "exited"

    second = _resting_up(registry, 10.0, 0.60, NOW - 100_000, "r2")
    _fill(registry, second, 10.0, 0.60, NOW - 100_000)
    with pytest.raises(lp.PairExitRefused, match="diverge"):
        lp._route_pair(venue, registry, lp.load_pair(registry, PAIR),
                       MAX_PAIR_COST, True, {TOK_UP: 10.0, TOK_DN: 0.0},
                       cfg=cfg, last_ms=NOW - 100_000,
                       now_s=NOW / 1000.0)


def test_fresh_fill_under_positive_grace_holds_instead(registry):
    """Grace still defers the first exit; the trap needs an exit behind it."""
    _ladder_market(registry, NOW)
    # Bid 0.57 keeps the leg out of adverse drift (loss 0.03 < 0.045 and
    # 5% < 10%), so grace -- not the drift trigger -- decides.
    venue = FakeVenue(best_ask=0.40, best_bid=0.57)

    first = _resting_up(registry, 10.0, 0.60, NOW, "r1")
    _fill(registry, first, 10.0, 0.60, NOW)
    routed = lp._route_pair(venue, registry, lp.load_pair(registry, PAIR),
                            MAX_PAIR_COST, True, {TOK_UP: 10.0, TOK_DN: 0.0},
                            cfg=_cfg(45.0), last_ms=NOW,
                            now_s=NOW / 1000.0 + 1.0)
    assert routed["action"] == "holding_grace"
    assert not any(c.startswith("sell:") for c in venue.calls)

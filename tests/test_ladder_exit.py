"""Timed ladder exit + ladder_exit method (issue #325): T4 RED first.

A ladder pair exits on the ladder timer (not single grace) with close
method ladder_exit; ladder_exit closes subtract inventory and net in
_refill sizing like other single-leg exits. No network in any test.
"""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

import pytest

from core_brain.order_registry import (
    CloseRecord, OrderRecord, FillRecord, OrderRegistry, QuoteRecord,
    inventory_from_registry,
)
from core_brain import single_buy_saver as lp

MAX_PAIR_COST = 0.995
TOK_UP = "tok-up"
TOK_DN = "tok-dn"
COND = "0xcond-ladder"
PAIR = "ladder-0xcond-ladder"
NOW = 2_000_000


@pytest.fixture
def registry(tmp_path):
    return OrderRegistry(db_path=tmp_path / "ladder_exit.db")


class FakeVenue:
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


def _ladder_pair(registry: OrderRegistry, when: int) -> None:
    down = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-dn", condition_id=COND,
        token_id=TOK_DN, side="BUY", price=0.38, original_size=10.0,
        status="open", posted_ts=when, last_polled_ts=when, pair_id=PAIR,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(down)
    up = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-up", condition_id=COND,
        token_id=TOK_UP, side="BUY", price=0.60, original_size=10.0,
        status="open", posted_ts=when, last_polled_ts=when, pair_id=PAIR,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(up)
    registry.record_fill(FillRecord(
        trade_id=f"trade-{up.id}", order_uuid=up.id, size=10.0,
        price=0.60, venue_ts=when,
    ))
    registry.update_order_status(up.id, status="filled", last_polled_ts=when)
    registry.log_quote(QuoteRecord(
        ts=when / 1000.0, condition_id=COND, token_id=TOK_UP, side="UP",
        price=0.60, size=10.0,
    ))
    registry.log_quote(QuoteRecord(
        ts=when / 1000.0, condition_id=COND, token_id=TOK_DN, side="DOWN",
        price=0.38, size=10.0,
    ))


def _cfg():
    return SimpleNamespace(
        ladder_mode=True, ladder_exit_sec=60.0,
        single_buy_grace_sec=0.0, pairs_exit_window_sec=900.0,
        single_buy_max_loss_pct=0.10, single_buy_max_loss_usd=0.045,
        max_pair_cost=MAX_PAIR_COST, max_order_usd=100.0,
    )


def test_ladder_exit_close_subtracts_inventory(registry):
    """A ladder_exit close removes the sold leg like other single exits."""
    _ladder_pair(registry, NOW - 200_000)
    before = inventory_from_registry(COND, TOK_UP, TOK_DN,
                                     db_path=registry.db_path)
    assert before.up_shares == pytest.approx(10.0)
    registry.log_close(CloseRecord(
        ts=time.time(), condition_id=COND, method="ladder_exit",
        shares=10.0, up_price=0.55, dn_price=None,
        cost_basis=4.9, proceeds=5.5, realized_pnl=0.6,
        up_cost_removed=4.9, dn_cost_removed=0.0,
    ))
    inv = inventory_from_registry(COND, TOK_UP, TOK_DN,
                                  db_path=registry.db_path)
    assert inv.up_shares == pytest.approx(0.0)


def test_prior_exit_shares_counts_ladder_exit(registry):
    """A ladder_exit close nets refill sizing like other single exits."""
    _ladder_pair(registry, NOW - 200_000)
    registry.log_close(CloseRecord(
        ts=time.time(), condition_id=COND, method="ladder_exit",
        shares=10.0, up_price=0.55, dn_price=None,
        cost_basis=4.9, proceeds=5.5, realized_pnl=0.6,
        up_cost_removed=4.9, dn_cost_removed=0.0,
    ))
    assert lp._prior_exit_shares(registry, COND, TOK_UP) == pytest.approx(10.0)
    assert lp._prior_exit_shares(registry, COND, TOK_DN) == pytest.approx(0.0)


def test_old_ladder_residue_exits_on_the_ladder_timer(registry):
    """A 200s-old ladder leg exits with the ladder_exit method."""
    _ladder_pair(registry, NOW - 200_000)
    venue = FakeVenue(best_ask=0.40, best_bid=0.57)
    routed = lp._route_pair(venue, registry, lp.load_pair(registry, PAIR),
                            MAX_PAIR_COST, True, {TOK_UP: 10.0, TOK_DN: 0.0},
                            cfg=_cfg(), last_ms=NOW - 200_000,
                            now_s=NOW / 1000.0)
    assert routed["action"] == "exited"
    assert routed["reason"] == "ladder_exit"
    methods = [c.get("method") for c in registry.get_all_closes()]
    assert methods == ["ladder_exit"]


def test_young_ladder_leg_holds_past_single_grace(registry):
    """A 10s-old ladder leg holds: the 60s ladder timer governs, not grace."""
    _ladder_pair(registry, NOW)
    venue = FakeVenue(best_ask=0.40, best_bid=0.57)
    routed = lp._route_pair(venue, registry, lp.load_pair(registry, PAIR),
                            MAX_PAIR_COST, True, {TOK_UP: 10.0, TOK_DN: 0.0},
                            cfg=_cfg(), last_ms=NOW,
                            now_s=NOW / 1000.0 + 10.0)
    assert routed["action"] == "holding_grace"
    assert not any(c.startswith("sell:") for c in venue.calls)

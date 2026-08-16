"""Unit tests for strategy/live_pairs.py — Stage 3 naked exit.

Stage 3 invariants:
1. The trigger fires at pair_cost >= max_pair_cost and holds one tick below it.
2. Cancel precedes sell. A failed cancel aborts before any sell is sent.
3. State is re-read between cancel and sell. A pair that completed in that
   window is routed to merge, never sold.
4. The sell size never exceeds what the registry says is held.
5. Registry/venue position divergence refuses the exit rather than acting on a
   view the venue does not share.
6. No network in any test.
"""

import uuid
import pytest
from pathlib import Path

from strategy.order_registry import OrderRegistry, OrderRecord, FillRecord
from strategy import live_pairs as lp


MAX_PAIR_COST = 0.995
TOK_UP = "tok-up"
TOK_DN = "tok-dn"
COND = "0xcond-stage3"


@pytest.fixture
def registry(tmp_path: Path) -> OrderRegistry:
    return OrderRegistry(db_path=tmp_path / "live.db")


def _one_sided_pair(registry: OrderRegistry, filled_size: float = 10.0,
                    fill_price: float = 0.60) -> str:
    """A heavy UP leg fully filled, a light DOWN leg still resting."""
    pair_id = "pair-1"
    now = 1_000_000

    heavy = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-heavy", condition_id=COND,
        token_id=TOK_UP, side="BUY", price=fill_price, original_size=filled_size,
        status="filled", posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(heavy)
    registry.record_fill(FillRecord(
        trade_id="trade-heavy", order_uuid=heavy.id, size=filled_size,
        price=fill_price, venue_ts=now,
    ))

    light = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-light", condition_id=COND,
        token_id=TOK_DN, side="BUY", price=0.38, original_size=filled_size,
        status="open", posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(light)
    return pair_id


class FakeClient:
    """Records every venue call. No network."""

    def __init__(self, best_ask=0.40, best_bid=0.55, cancel_ok=True,
                 bid_depth=100.0):
        self.best_ask = best_ask
        self.best_bid = best_bid
        self.cancel_ok = cancel_ok
        self.bid_depth = bid_depth
        self.calls: list[str] = []
        self.creds = object()

    def get_order_book(self, token_id):
        self.calls.append(f"book:{token_id}")
        return {
            "asset_id": token_id,
            "bids": [{"price": str(self.best_bid), "size": str(self.bid_depth)}],
            "asks": [{"price": str(self.best_ask), "size": "100"}],
        }

    def cancel_order(self, payload):
        self.calls.append(f"cancel:{getattr(payload, 'orderID', payload)}")
        if not self.cancel_ok:
            raise RuntimeError("venue refused the cancel")
        return {"canceled": ["venue-light"]}

    def create_and_post_market_order(self, order_args, options=None,
                                     order_type="FOK", defer_exec=False):
        self.calls.append(
            f"sell:{order_args.token_id}:{order_args.amount}:{order_args.side}"
        )
        return {"success": True, "orderID": "venue-sell"}


# ---------------------------------------------------------------------------
# Invariant 1 — the trigger
# ---------------------------------------------------------------------------


def test_trigger_fires_at_the_cap():
    """0.60 fill + 0.395 ask == 0.995 == max_pair_cost. `>=`, not `>`."""
    assert lp.should_exit(fill_cost=0.60, light_ask=0.395,
                          max_pair_cost=MAX_PAIR_COST) is True


def test_trigger_holds_one_tick_below_the_cap():
    assert lp.should_exit(fill_cost=0.60, light_ask=0.394,
                          max_pair_cost=MAX_PAIR_COST) is False


def test_trigger_fires_when_the_light_leg_has_no_ask():
    """No ask at all is not a completable pair; it is a naked leg."""
    assert lp.should_exit(fill_cost=0.60, light_ask=None,
                          max_pair_cost=MAX_PAIR_COST) is True


# ---------------------------------------------------------------------------
# Invariant 2 — cancel before sell, and a failed cancel aborts
# ---------------------------------------------------------------------------


def test_cancel_precedes_the_sell(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry)
    client = FakeClient(best_ask=0.40)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=True)

    assert result["action"] == "exited"
    cancel_i = next(i for i, c in enumerate(client.calls) if c.startswith("cancel:"))
    sell_i = next(i for i, c in enumerate(client.calls) if c.startswith("sell:"))
    assert cancel_i < sell_i, "selling first leaves a resting order that can refill"


def test_a_failed_cancel_aborts_before_any_sell(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry)
    client = FakeClient(best_ask=0.40, cancel_ok=False)

    with pytest.raises(lp.PairExitRefused, match="cancel"):
        lp.exit_naked_leg(client, registry, pair_id,
                          max_pair_cost=MAX_PAIR_COST, live=True)

    assert not any(c.startswith("sell:") for c in client.calls)


# ---------------------------------------------------------------------------
# Invariant 3 — re-read between cancel and sell
# ---------------------------------------------------------------------------


def test_a_pair_that_completed_between_cancel_and_sell_routes_to_merge(
    registry: OrderRegistry,
):
    """The cancel can race a match that already happened.

    Market-selling one leg of a now-complete pair converts a position worth
    $1.00 at merge into a realized loss. That is the worst outcome on this path.
    """
    pair_id = _one_sided_pair(registry)
    light = next(o for o in registry.get_active_orders() if o.token_id == TOK_DN)

    class RacingClient(FakeClient):
        def cancel_order(self, payload):
            out = super().cancel_order(payload)
            # The other leg filled while the cancel was in flight.
            registry.record_fill(FillRecord(
                trade_id="trade-light", order_uuid=light.id, size=10.0,
                price=0.38, venue_ts=1_000_100,
            ))
            registry.update_order_status(light.id, "filled", 1_000_100)
            return out

    client = RacingClient(best_ask=0.40)
    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=True)

    assert result["action"] == "route_to_merge"
    assert not any(c.startswith("sell:") for c in client.calls)


# ---------------------------------------------------------------------------
# Invariant 4 — never sell more than the registry says is held
# ---------------------------------------------------------------------------


def test_sell_size_is_capped_by_the_registry(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40, bid_depth=1_000.0)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=True)

    assert result["size"] == pytest.approx(10.0)
    sell_call = next(c for c in client.calls if c.startswith("sell:"))
    assert sell_call.split(":")[2] == "10.0"


def test_sell_size_is_capped_by_bid_depth(registry: OrderRegistry):
    """Depth below the held size is a partial exit, not an oversized one."""
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40, bid_depth=4.0)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=True)

    assert result["size"] == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Invariant 5 — registry/venue divergence refuses
# ---------------------------------------------------------------------------


def test_position_divergence_refuses_the_exit(registry: OrderRegistry):
    """The Data API says we hold less than the registry believes.

    Selling the registry's number would be selling shares we may not have.
    """
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40)

    with pytest.raises(lp.PairExitRefused, match="diverge"):
        lp.exit_naked_leg(client, registry, pair_id,
                          max_pair_cost=MAX_PAIR_COST, live=True,
                          venue_positions={TOK_UP: 3.0})

    assert not any(c.startswith("cancel:") for c in client.calls)
    assert not any(c.startswith("sell:") for c in client.calls)


def test_matching_positions_allow_the_exit(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=True,
                               venue_positions={TOK_UP: 10.0})
    assert result["action"] == "exited"


def test_divergence_check_tolerates_float_dust(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=True,
                               venue_positions={TOK_UP: 10.0 - 1e-9})
    assert result["action"] == "exited"


# ---------------------------------------------------------------------------
# Dry run and no-trigger paths
# ---------------------------------------------------------------------------


def test_below_the_cap_holds_and_sends_nothing(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.30)  # 0.60 + 0.30 = 0.90, well under

    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=True)

    assert result["action"] == "hold"
    assert not any(c.startswith(("cancel:", "sell:")) for c in client.calls)


def test_dry_run_sends_no_venue_writes(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry)
    client = FakeClient(best_ask=0.40)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=False)

    assert result["action"] == "would_exit"
    assert not any(c.startswith(("cancel:", "sell:")) for c in client.calls)


def test_a_balanced_pair_is_not_an_exit_candidate(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry)
    light = next(o for o in registry.get_active_orders() if o.token_id == TOK_DN)
    registry.record_fill(FillRecord(
        trade_id="trade-light-pre", order_uuid=light.id, size=10.0,
        price=0.38, venue_ts=1_000_050,
    ))
    registry.update_order_status(light.id, "filled", 1_000_050)

    client = FakeClient(best_ask=0.40)
    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=True)

    assert result["action"] == "balanced"
    assert not any(c.startswith(("cancel:", "sell:")) for c in client.calls)

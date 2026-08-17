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
                 bid_depth=100.0, ask_depth=100.0, bid_levels=None,
                 venue_matched=None, get_order_ok=True, tick_size="0.01"):
        self.best_ask = best_ask
        self.best_bid = best_bid
        self.cancel_ok = cancel_ok
        self.bid_depth = bid_depth
        self.ask_depth = ask_depth
        self.bid_levels = bid_levels
        self.tick_size = tick_size
        # What the VENUE says each order has matched. Deliberately separate from
        # the registry: the whole point of the post-cancel read is that the two
        # can disagree until a reconcile pass lands.
        self.venue_matched = dict(venue_matched or {})
        self.get_order_ok = get_order_ok
        self.calls: list[str] = []
        self.orders: list[dict] = []
        self.creds = object()

    def get_order_book(self, token_id):
        self.calls.append(f"book:{token_id}")
        asks = ([] if self.best_ask is None
                else [{"price": str(self.best_ask), "size": str(self.ask_depth)}])
        bids = (self.bid_levels if self.bid_levels is not None
                else [{"price": str(self.best_bid), "size": str(self.bid_depth)}])
        return {
            "asset_id": token_id,
            "bids": bids,
            "asks": asks,
            "tick_size": self.tick_size,
        }

    def get_order(self, order_id):
        self.calls.append(f"get_order:{order_id}")
        if not self.get_order_ok:
            raise RuntimeError("venue order read failed")
        return {"orderID": order_id,
                "size_matched": self.venue_matched.get(order_id, 0.0)}

    def cancel_order(self, payload):
        self.calls.append(f"cancel:{getattr(payload, 'orderID', payload)}")
        if not self.cancel_ok:
            raise RuntimeError("venue refused the cancel")
        return {"canceled": ["venue-light"]}

    def create_and_post_market_order(self, order_args, options=None,
                                     order_type="FOK", defer_exec=False):
        verb = "sell" if order_args.side == "SELL" else "buy"
        self.calls.append(
            f"{verb}:{order_args.token_id}:{order_args.amount}:{order_args.side}"
        )
        # Kept structured as well as stringified: `amount` means shares on a
        # SELL and USDC on a BUY, and a test that only reads the string cannot
        # tell those apart.
        self.orders.append({
            "side": order_args.side,
            "token_id": order_args.token_id,
            "amount": order_args.amount,
            "price": getattr(order_args, "price", None),
        })
        return {"success": True, "orderID": f"venue-{verb}"}


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
            # The other leg filled while the cancel was in flight. Only the
            # VENUE knows -- the registry learns at the next reconcile pass,
            # which is exactly the window this test covers.
            self.venue_matched["venue-light"] = 10.0
            return out

    client = RacingClient(best_ask=0.40)
    assert light is not None
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


# ---------------------------------------------------------------------------
# Stage 4 — second-leg completion
#
# Crossing to complete a half-open pair reduces exposure: the result is worth
# $1.00 at merge. It must never do the stop-loss's job badly, so it refuses any
# cross that would push the pair past the cap.
# ---------------------------------------------------------------------------


def test_completion_crosses_when_the_pair_stays_under_the_cap(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30)  # 0.60 + 0.30 = 0.90 < 0.995

    result = lp.complete_pair(client, registry, pair_id,
                              max_pair_cost=MAX_PAIR_COST, live=True)

    assert result["action"] == "completed"
    assert result["size"] == pytest.approx(10.0)
    assert result["pair_cost"] == pytest.approx(0.90)
    buy = next(c for c in client.calls if c.startswith("buy:"))
    assert buy.split(":")[1] == TOK_DN


def test_completion_refuses_a_cross_that_breaches_the_cap(registry: OrderRegistry):
    """That is the stop-loss's job, and this path must not do it badly."""
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.40)  # 0.60 + 0.40 = 1.00 >= 0.995

    with pytest.raises(lp.PairCompletionRefused, match="max_pair_cost"):
        lp.complete_pair(client, registry, pair_id,
                         max_pair_cost=MAX_PAIR_COST, live=True)

    assert not any(c.startswith("buy:") for c in client.calls)


def test_completion_sizes_from_fills_not_from_intent(registry: OrderRegistry):
    """A leg that filled 4 of 10 is a 4-share position.

    Completing 10 would open 6 shares of fresh exposure on the other side --
    the opposite of what this path is for.
    """
    pair_id = "pair-partial"
    now = 1_000_000
    heavy = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-heavy-p", condition_id=COND,
        token_id=TOK_UP, side="BUY", price=0.60, original_size=10.0,
        status="partial", posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(heavy)
    registry.record_fill(FillRecord(
        trade_id="trade-partial", order_uuid=heavy.id, size=4.0,
        price=0.60, venue_ts=now,
    ))
    light = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-light-p", condition_id=COND,
        token_id=TOK_DN, side="BUY", price=0.30, original_size=10.0,
        status="open", posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(light)

    client = FakeClient(best_ask=0.30)
    result = lp.complete_pair(client, registry, pair_id,
                              max_pair_cost=MAX_PAIR_COST, live=True)

    assert result["size"] == pytest.approx(4.0)


def test_completion_is_capped_by_max_order_usd(registry: OrderRegistry):
    """The Stage 1 notional cap applies to this order like any other."""
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30)

    with pytest.raises(lp.PairCompletionRefused, match="MAX_ORDER_USD"):
        lp.complete_pair(client, registry, pair_id,
                         max_pair_cost=MAX_PAIR_COST, live=True,
                         max_order_usd=1.00)  # 10 * 0.30 = $3.00


def test_completion_is_capped_by_ask_depth(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30, ask_depth=3.0)

    result = lp.complete_pair(client, registry, pair_id,
                              max_pair_cost=MAX_PAIR_COST, live=True)
    assert result["size"] == pytest.approx(3.0)


def test_completion_refuses_without_an_ask(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=None)

    with pytest.raises(lp.PairCompletionRefused, match="no ask"):
        lp.complete_pair(client, registry, pair_id,
                         max_pair_cost=MAX_PAIR_COST, live=True)


def test_completion_dry_run_sends_nothing(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30)

    result = lp.complete_pair(client, registry, pair_id,
                              max_pair_cost=MAX_PAIR_COST, live=False)

    assert result["action"] == "would_complete"
    assert not any(c.startswith("buy:") for c in client.calls)


def test_a_balanced_pair_needs_no_completion(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry)
    light = next(o for o in registry.get_active_orders() if o.token_id == TOK_DN)
    registry.record_fill(FillRecord(
        trade_id="trade-light-done", order_uuid=light.id, size=10.0,
        price=0.30, venue_ts=1_000_050,
    ))
    registry.update_order_status(light.id, "filled", 1_000_050)

    client = FakeClient(best_ask=0.30)
    result = lp.complete_pair(client, registry, pair_id,
                              max_pair_cost=MAX_PAIR_COST, live=True)

    assert result["action"] == "balanced"
    assert not any(c.startswith("buy:") for c in client.calls)


def test_a_completed_pair_reads_as_held_on_both_legs(registry: OrderRegistry):
    """The acceptance condition: merge's pre-flight must see both legs.

    Completion is only worth doing if the result is mergeable, so the check is
    on the registry's own view of holdings per token, which is what the merge
    pre-flight reconciles against.
    """
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    light = next(o for o in registry.get_active_orders() if o.token_id == TOK_DN)
    client = FakeClient(best_ask=0.30)

    lp.complete_pair(client, registry, pair_id,
                     max_pair_cost=MAX_PAIR_COST, live=True)
    # The venue fill arrives through reconcile; simulate that landing.
    registry.record_fill(FillRecord(
        trade_id="trade-completion", order_uuid=light.id, size=10.0,
        price=0.30, venue_ts=1_000_200,
    ))

    pair = lp.load_pair(registry, pair_id)
    assert pair["naked"] == pytest.approx(0.0)
    assert pair["heavy"]["matched"] == pytest.approx(10.0)
    assert pair["light"]["matched"] == pytest.approx(10.0)


def test_completion_buy_amount_is_usdc_not_shares(registry: OrderRegistry):
    """MarketOrderArgsV2.amount is the maker amount -- USDC on a BUY.

    The SDK computes shares received as amount / price, so passing a share
    count submits a much larger buy than intended: 10 shares at $0.30 becomes a
    $10.00 order acquiring ~33 shares, which is 23 shares of fresh exposure on
    the leg this path exists to close. Every guard above the send validates the
    $3.00 we meant, so nothing else catches the unit.
    """
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30)

    result = lp.complete_pair(client, registry, pair_id,
                              max_pair_cost=MAX_PAIR_COST, live=True)

    sent = next(o for o in client.orders if o["side"] == "BUY")
    assert sent["amount"] == pytest.approx(3.00)            # 10 shares * $0.30
    assert sent["amount"] == pytest.approx(result["size"] * result["ask"])
    assert sent["amount"] != pytest.approx(result["size"])  # not the share count
    assert sent["price"] == pytest.approx(0.30)


def test_exit_sell_amount_stays_in_shares(registry: OrderRegistry):
    """The mirror of the BUY case: on a SELL, amount is the share count.

    Same field, opposite unit. Asserted so a future fix to one side cannot
    quietly convert the other.
    """
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40)

    lp.exit_naked_leg(client, registry, pair_id,
                      max_pair_cost=MAX_PAIR_COST, live=True)

    sent = next(o for o in client.orders if o["side"] == "SELL")
    assert sent["amount"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Review round 2 — the holes the first pass left
# ---------------------------------------------------------------------------


def test_exit_cancels_working_orders_on_the_heavy_leg_too(registry: OrderRegistry):
    """A `partial` heavy leg still has working size that can refill after the sell."""
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    now = 1_000_500
    extra = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-heavy-2", condition_id=COND,
        token_id=TOK_UP, side="BUY", price=0.60, original_size=5.0,
        status="partial", posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(extra)

    client = FakeClient(best_ask=0.40)
    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=True)

    assert "venue-heavy-2" in result["cancelled"]
    cancel_idx = [i for i, c in enumerate(client.calls) if c.startswith("cancel:")]
    sell_idx = next(i for i, c in enumerate(client.calls) if c.startswith("sell:"))
    assert max(cancel_idx) < sell_idx, "every working order must be quiet before the sell"


def test_exit_refuses_when_the_venue_order_read_fails(registry: OrderRegistry):
    """An unreadable order is not an unfilled one."""
    pair_id = _one_sided_pair(registry)
    client = FakeClient(best_ask=0.40, get_order_ok=False)

    with pytest.raises(lp.PairExitRefused, match="venue state"):
        lp.exit_naked_leg(client, registry, pair_id,
                          max_pair_cost=MAX_PAIR_COST, live=True)

    assert not any(c.startswith("sell:") for c in client.calls)


def test_exit_bounds_the_sell_price_and_counts_only_acceptable_depth(
    registry: OrderRegistry,
):
    """Depth below the slippage floor is not depth we would accept.

    Best bid 0.55, floor 0.53. The 40 shares resting at 0.40 must not be
    counted, and the submitted price must be the floor, not the best bid.
    """
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40, bid_levels=[
        {"price": "0.55", "size": "3"},
        {"price": "0.54", "size": "2"},
        {"price": "0.40", "size": "40"},
    ])

    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=True)

    assert result["size"] == pytest.approx(5.0)        # 3 + 2, not 45
    assert result["min_price"] == pytest.approx(0.53)  # 0.55 - 0.02, on tick
    sent = next(o for o in client.orders if o["side"] == "SELL")
    assert sent["price"] == pytest.approx(0.53)


def test_completion_cancels_the_resting_light_buy_before_crossing(
    registry: OrderRegistry,
):
    """Otherwise the maker BUY and the taker BUY can both fill and double the leg."""
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30)

    result = lp.complete_pair(client, registry, pair_id,
                              max_pair_cost=MAX_PAIR_COST, live=True)

    assert "venue-light" in result["cancelled"]
    cancel_i = next(i for i, c in enumerate(client.calls) if c.startswith("cancel:"))
    buy_i = next(i for i, c in enumerate(client.calls) if c.startswith("buy:"))
    assert cancel_i < buy_i


def test_completion_shrinks_to_the_remainder_after_a_partial_race(
    registry: OrderRegistry,
):
    """If the light leg partly filled during the cancel, cross only the rest."""
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30, venue_matched={"venue-light": 6.0})

    result = lp.complete_pair(client, registry, pair_id,
                              max_pair_cost=MAX_PAIR_COST, live=True)

    assert result["size"] == pytest.approx(4.0)
    sent = next(o for o in client.orders if o["side"] == "BUY")
    assert sent["amount"] == pytest.approx(4.0 * 0.30)


def test_completion_is_a_no_op_when_the_leg_filled_during_the_cancel(
    registry: OrderRegistry,
):
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30, venue_matched={"venue-light": 10.0})

    result = lp.complete_pair(client, registry, pair_id,
                              max_pair_cost=MAX_PAIR_COST, live=True)

    assert result["action"] == "balanced"
    assert not any(c.startswith("buy:") for c in client.calls)


def test_a_venue_surplus_does_not_block_the_exit(registry: OrderRegistry):
    """Holding more than the registry believes cannot cause an oversell.

    The same token may be held by another pair, or part of a position already
    merged. Refusing here would block the one action that closes exposure.
    """
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=True,
                               venue_positions={TOK_UP: 25.0})
    assert result["action"] == "exited"


def test_a_token_absent_from_the_positions_read_refuses(registry: OrderRegistry):
    """Absence is not zero -- it is equally consistent with a filtered read."""
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40)

    with pytest.raises(lp.PairExitRefused, match="no position at all"):
        lp.exit_naked_leg(client, registry, pair_id,
                          max_pair_cost=MAX_PAIR_COST, live=True,
                          venue_positions={"some-other-token": 5.0})

    assert not any(c.startswith("cancel:") for c in client.calls)

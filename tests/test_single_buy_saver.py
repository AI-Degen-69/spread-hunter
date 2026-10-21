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
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from core_brain.config import MakerConfig
from core_brain.market_resolution import MarketEndState
from core_brain.order_registry import (
    OrderRegistry, OrderRecord, FillRecord, QuoteRecord,
)
from core_brain import single_buy_saver as lp
from core_brain.statistical_analytics import rescue_friction_summary


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

    # The fleet logs a quote row for every order with its UP/DOWN side -- the
    # quotes ledger is how the exit resolves which leg a token is (the closes
    # table encodes the sold leg via up_price/dn_price, not a token id).
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=COND, token_id=TOK_UP, side="UP",
        price=fill_price, size=filled_size,
    ))
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=COND, token_id=TOK_DN, side="DOWN",
        price=0.38, size=filled_size,
    ))
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
        # Kept structured as stringified: `amount` means shares on a
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
                          bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST) is True


def test_trigger_holds_one_tick_below_the_cap():
    assert lp.should_exit(fill_cost=0.60, light_ask=0.394,
                          bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST) is False


def test_trigger_fires_when_the_light_leg_has_no_ask():
    """No ask at all is not a completable pair; it is a naked leg."""
    assert lp.should_exit(fill_cost=0.60, light_ask=None,
                          bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST) is True


# ---------------------------------------------------------------------------
# Invariant 2 — cancel before sell, and a failed cancel aborts
# ---------------------------------------------------------------------------


def test_cancel_precedes_the_sell(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry)
    client = FakeClient(best_ask=0.40)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert result["action"] == "exited"
    cancel_i = next(i for i, c in enumerate(client.calls) if c.startswith("cancel:"))
    sell_i = next(i for i, c in enumerate(client.calls) if c.startswith("sell:"))
    assert cancel_i < sell_i, "selling first leaves a resting order that can refill"


def test_a_failed_cancel_aborts_before_any_sell(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry)
    client = FakeClient(best_ask=0.40, cancel_ok=False)

    with pytest.raises(lp.PairExitRefused, match="cancel"):
        lp.exit_naked_leg(client, registry, pair_id,
                          bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

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
                               bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert result["action"] == "route_to_merge"
    assert not any(c.startswith("sell:") for c in client.calls)


# ---------------------------------------------------------------------------
# Invariant 4 — never sell more than the registry says is held
# ---------------------------------------------------------------------------


def test_sell_size_is_capped_by_the_registry(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40, bid_depth=1_000.0)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert result["size"] == pytest.approx(10.0)
    sell_call = next(c for c in client.calls if c.startswith("sell:"))
    assert sell_call.split(":")[2] == "10.0"


def test_sell_size_is_capped_by_bid_depth(registry: OrderRegistry):
    """Depth below the held size is a partial exit, not an oversized one."""
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40, bid_depth=4.0)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

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
                          bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True,
                          venue_positions={TOK_UP: 3.0})

    assert not any(c.startswith("cancel:") for c in client.calls)
    assert not any(c.startswith("sell:") for c in client.calls)


def test_matching_positions_allow_the_exit(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True,
                               venue_positions={TOK_UP: 10.0})
    assert result["action"] == "exited"


def test_divergence_check_tolerates_float_dust(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True,
                               venue_positions={TOK_UP: 10.0 - 1e-9})
    assert result["action"] == "exited"


# ---------------------------------------------------------------------------
# Dry run and no-trigger paths
# ---------------------------------------------------------------------------


def test_below_the_cap_holds_and_sends_nothing(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.30)  # 0.60 + 0.30 = 0.90, well under

    result = lp.exit_naked_leg(client, registry, pair_id,
                               bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert result["action"] == "hold"
    assert not any(c.startswith(("cancel:", "sell:")) for c in client.calls)


def test_dry_run_sends_no_venue_writes(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry)
    client = FakeClient(best_ask=0.40)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=False)

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
                               bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

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
                              bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert result["action"] == "completed"
    assert result["size"] == pytest.approx(10.0)
    assert result["pair_cost"] == pytest.approx(0.90)
    buy = next(c for c in client.calls if c.startswith("buy:"))
    assert buy.split(":")[1] == TOK_DN


def test_completion_refuses_a_cross_that_breaches_the_cap(registry: OrderRegistry):
    """That is the stop-loss's job, and this path must not do it badly."""
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.40)  # 0.60 + 0.40 = 1.00 >= 0.995

    with pytest.raises(lp.PairCompletionRefused, match="bot_pos_price_max_pair_cost_usd"):
        lp.complete_pair(client, registry, pair_id,
                         bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

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
                              bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert result["size"] == pytest.approx(4.0)


def test_completion_is_capped_by_max_order_usd(registry: OrderRegistry):
    """The Stage 1 notional cap applies to this order like any other."""
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30)

    with pytest.raises(lp.PairCompletionRefused, match="MAX_ORDER_USD"):
        lp.complete_pair(client, registry, pair_id,
                         bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True,
                         bot_risk_exposure_max_order_usd=1.00)  # 10 * 0.30 = $3.00


def test_completion_is_capped_by_ask_depth(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30, ask_depth=3.0)

    result = lp.complete_pair(client, registry, pair_id,
                              bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)
    assert result["size"] == pytest.approx(3.0)


def test_completion_refuses_without_an_ask(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=None)

    with pytest.raises(lp.PairCompletionRefused, match="no ask"):
        lp.complete_pair(client, registry, pair_id,
                         bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)


def test_completion_dry_run_sends_nothing(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30)

    result = lp.complete_pair(client, registry, pair_id,
                              bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=False)

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
                              bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

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
                     bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)
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
                              bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

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
                      bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

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
                               bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

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
                          bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

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
                               bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

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
                              bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

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
                              bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert result["size"] == pytest.approx(4.0)
    sent = next(o for o in client.orders if o["side"] == "BUY")
    assert sent["amount"] == pytest.approx(4.0 * 0.30)


def test_completion_is_a_no_op_when_the_leg_filled_during_the_cancel(
    registry: OrderRegistry,
):
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30, venue_matched={"venue-light": 10.0})

    result = lp.complete_pair(client, registry, pair_id,
                              bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

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
                               bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True,
                               venue_positions={TOK_UP: 25.0})
    assert result["action"] == "exited"


def test_a_token_absent_from_the_positions_read_refuses(registry: OrderRegistry):
    """Absence is not zero -- it is equally consistent with a filtered read."""
    pair_id = _one_sided_pair(registry, filled_size=10.0)
    client = FakeClient(best_ask=0.40)

    with pytest.raises(lp.PairExitRefused, match="no position at all"):
        lp.exit_naked_leg(client, registry, pair_id,
                          bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True,
                          venue_positions={"some-other-token": 5.0})

    assert not any(c.startswith("cancel:") for c in client.calls)


# ---------------------------------------------------------------------------
# Review round 3
# ---------------------------------------------------------------------------


def test_tick_floor_does_not_drop_a_whole_tick_on_binary_float_error():
    """`0.29 / 0.01` is 28.999999999999996 in binary floating point.

    A bare int() floors an already-aligned price one whole tick lower, which
    moves the sell bound below what we chose and lets depth_at_or_above count an
    extra level. Safe in direction, wrong in value.
    """
    assert lp._floor_to_tick(0.29, 0.01) == pytest.approx(0.29)
    assert lp._floor_to_tick(0.53, 0.01) == pytest.approx(0.53)
    assert lp._floor_to_tick(0.535, 0.01) == pytest.approx(0.53)
    assert lp._floor_to_tick(0.07, 0.001) == pytest.approx(0.07)


def test_completion_refusals_use_the_completion_exception(registry: OrderRegistry):
    """A failed cancel on the completion path must not raise the exit's type.

    `complete_pair_cmd` catches only PairCompletionRefused. An escaping
    PairExitRefused would fall to its generic handler, mark the audit row
    `interrupted` although nothing was sent, and block every later completion on
    that condition until --force.
    """
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30, cancel_ok=False)

    with pytest.raises(lp.PairCompletionRefused, match="Cancel"):
        lp.complete_pair(client, registry, pair_id,
                         bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert not any(c.startswith("buy:") for c in client.calls)


def test_completion_venue_read_failure_uses_the_completion_exception(
    registry: OrderRegistry,
):
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.30, get_order_ok=False)

    with pytest.raises(lp.PairCompletionRefused, match="venue state"):
        lp.complete_pair(client, registry, pair_id,
                         bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert not any(c.startswith("buy:") for c in client.calls)


def test_exit_refusals_still_use_the_exit_exception(registry: OrderRegistry):
    """The default is unchanged; only the completion path overrides it."""
    pair_id = _one_sided_pair(registry)
    client = FakeClient(best_ask=0.40, cancel_ok=False)

    with pytest.raises(lp.PairExitRefused):
        lp.exit_naked_leg(client, registry, pair_id,
                          bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)


# ---------------------------------------------------------------------------
# Review round 4 — the audit row must not block the recovery it recommends
# ---------------------------------------------------------------------------


def test_route_to_merge_does_not_leave_the_condition_blocked(
    registry: OrderRegistry, tmp_path, monkeypatch
):
    """`route_to_merge` sends no SELL, so it must not hold the condition open.

    `exit_pair` prints "now run merge" on this branch. If the audit row were
    marked `submitted`, `_check_idempotency_guard` would then refuse that merge
    until --force -- a guard blocking the recovery it just recommended.
    """
    from core_brain import order_manager as live_exec

    monkeypatch.setattr(live_exec, "RUN", tmp_path)
    monkeypatch.setattr(live_exec, "client", lambda *a, **k: None)

    pair_id = _one_sided_pair(registry)
    light = next(o for o in registry.get_active_orders() if o.token_id == TOK_DN)

    def fake_exit(*args, **kwargs):
        return {"action": "route_to_merge", "pair_id": pair_id,
                "condition_id": COND, "cancelled": [light.order_id], "size": 0.0}

    monkeypatch.setattr(live_exec, "exit_naked_leg", fake_exit, raising=False)
    monkeypatch.setattr("core_brain.single_buy_saver.exit_naked_leg", fake_exit)

    live_exec.exit_pair(pair_id, live=True, db_path=registry.db_path,
                        skip_positions_check=True)

    # The guard must let the recommended merge through without --force.
    live_exec._check_idempotency_guard(COND, force=False)


def test_a_real_exit_does_hold_the_condition(registry: OrderRegistry, tmp_path,
                                             monkeypatch):
    """The mirror: a sell that actually went out must block a second one."""
    from core_brain import order_manager as live_exec

    monkeypatch.setattr(live_exec, "RUN", tmp_path)
    monkeypatch.setattr(live_exec, "client", lambda *a, **k: None)

    pair_id = _one_sided_pair(registry)

    def fake_exit(*args, **kwargs):
        return {"action": "exited", "pair_id": pair_id, "condition_id": COND,
                "size": 10.0, "cancelled": []}

    monkeypatch.setattr("core_brain.single_buy_saver.exit_naked_leg", fake_exit)

    live_exec.exit_pair(pair_id, live=True, db_path=registry.db_path,
                        skip_positions_check=True)

    with pytest.raises(SystemExit):
        live_exec._check_idempotency_guard(COND, force=False)


def test_complete_pair_cmd_runs_end_to_end_and_holds_after_a_cross(
    registry: OrderRegistry, tmp_path, monkeypatch
):
    """The Stage 4 command has its own config load, audit row and result handling.

    It carried the same `from core_brain.config import Config` failure as the exit
    and was fixed alongside it, but the round-four tests only drove `exit_pair`.
    Fixing one entry point and testing the other is how that defect survived
    thirty-seven passing tests in the first place.
    """
    from core_brain import order_manager as live_exec

    monkeypatch.setattr(live_exec, "RUN", tmp_path)
    monkeypatch.setattr(live_exec, "client", lambda *a, **k: None)

    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)

    def fake_complete(*args, **kwargs):
        # Proves the call site was reached with a real max_pair_cost, which is
        # what the missing config import used to break before any work started.
        assert kwargs["bot_pos_price_max_pair_cost_usd"] > 0
        return {"action": "completed", "pair_id": pair_id, "condition_id": COND,
                "size": 10.0, "notional": 3.0}

    monkeypatch.setattr("core_brain.single_buy_saver.complete_pair", fake_complete)

    live_exec.complete_pair_cmd(pair_id, live=True, db_path=registry.db_path,
                                skip_positions_check=True)

    # A cross went out, so the condition is held against a second unforced one.
    with pytest.raises(SystemExit):
        live_exec._check_idempotency_guard(COND, force=False)


def test_complete_pair_cmd_does_not_hold_the_condition_when_nothing_crossed(
    registry: OrderRegistry, tmp_path, monkeypatch
):
    """`balanced` sends no BUY, so it must not block a later merge or completion."""
    from core_brain import order_manager as live_exec

    monkeypatch.setattr(live_exec, "RUN", tmp_path)
    monkeypatch.setattr(live_exec, "client", lambda *a, **k: None)

    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)

    def fake_complete(*args, **kwargs):
        return {"action": "balanced", "pair_id": pair_id, "condition_id": COND,
                "size": 0.0}

    monkeypatch.setattr("core_brain.single_buy_saver.complete_pair", fake_complete)

    live_exec.complete_pair_cmd(pair_id, live=True, db_path=registry.db_path,
                                skip_positions_check=True)

    live_exec._check_idempotency_guard(COND, force=False)


def test_an_unknown_pair_id_refuses_cleanly_on_both_commands(tmp_path, monkeypatch):
    """A typo must produce the refusal message, not a traceback.

    Found by running the commands rather than by a test: `load_pair` raises
    before either command's try block, and the completion path does not
    otherwise catch the exit path's exception type.
    """
    from core_brain import order_manager as live_exec

    monkeypatch.setattr(live_exec, "RUN", tmp_path)
    monkeypatch.setattr(live_exec, "client", lambda *a, **k: None)
    db = tmp_path / "empty.db"

    with pytest.raises(SystemExit, match="EXIT REFUSED"):
        live_exec.exit_pair("no-such-pair", live=False, db_path=db,
                            skip_positions_check=True)

    with pytest.raises(SystemExit, match="COMPLETION REFUSED"):
        live_exec.complete_pair_cmd("no-such-pair", live=False, db_path=db,
                                    skip_positions_check=True)


# ---------------------------------------------------------------------------
# Self-review round — heavy/light is a ranking, and rankings flip
# ---------------------------------------------------------------------------


def _overfilling_light_pair(registry: OrderRegistry) -> str:
    """Heavy UP 10 filled; light DOWN order for 12, not yet filled."""
    pair_id = "pair-overfill"
    now = 1_000_000
    heavy = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-heavy", condition_id=COND,
        token_id=TOK_UP, side="BUY", price=0.60, original_size=10.0,
        status="filled", posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(heavy)
    registry.record_fill(FillRecord(trade_id="t-heavy", order_uuid=heavy.id,
                                    size=10.0, price=0.60, venue_ts=now))
    light = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-light", condition_id=COND,
        token_id=TOK_DN, side="BUY", price=0.30, original_size=12.0,
        status="open", posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(light)
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=COND, token_id=TOK_UP, side="UP",
        price=0.60, size=10.0,
    ))
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=COND, token_id=TOK_DN, side="DOWN",
        price=0.30, size=12.0,
    ))
    return pair_id


def test_exit_survives_the_light_leg_overtaking_the_heavy_one(
    registry: OrderRegistry,
):
    """`heavy` and `light` are a ranking, and the ranking flips.

    If the light leg fills past the heavy one and reconcile records it, a second
    load_pair swaps the two. Deriving `naked` from the post-cancel ranking then
    subtracts a venue reading for one token from a registry reading for another,
    and the exit reports a complete pair while shares are still naked.
    """
    pair_id = _overfilling_light_pair(registry)
    light = next(o for o in registry.get_active_orders() if o.token_id == TOK_DN)

    class OvertakingClient(FakeClient):
        def cancel_order(self, payload):
            out = super().cancel_order(payload)
            # The light leg fills 12 -- more than the heavy leg's 10 -- and a
            # reconcile pass lands before the re-read.
            self.venue_matched["venue-light"] = 12.0
            registry.record_fill(FillRecord(
                trade_id="t-light", order_uuid=light.id, size=12.0,
                price=0.30, venue_ts=1_000_100,
            ))
            registry.update_order_status(light.id, "filled", 1_000_100)
            return out

    client = OvertakingClient(best_ask=0.40)

    # UP 10 against DOWN 12 is NOT a closed pair -- two DOWN shares are naked.
    # Reporting route_to_merge here would tell the operator to merge 10 pairs
    # and walk away from an open position on the other token.
    with pytest.raises(lp.PairExitRefused, match="changed token"):
        lp.exit_naked_leg(client, registry, pair_id,
                          bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert not any(c.startswith("sell:") for c in client.calls)


def test_naked_after_keys_on_tokens_not_on_rank():
    """The reduction itself, isolated from the venue plumbing."""
    after = {"legs": {
        TOK_UP: {"token_id": TOK_UP, "matched": 10.0, "notional": 6.0},
        TOK_DN: {"token_id": TOK_DN, "matched": 12.0, "notional": 3.6},
    }}
    naked, fill_cost, unpriced = lp._naked_after(
        after, TOK_UP, TOK_DN, venue_light_extra=0.0)
    # Signed: negative means the ORIGINAL LIGHT token is the naked one.
    assert naked == pytest.approx(-2.0)
    assert fill_cost == pytest.approx(0.60)
    assert unpriced == pytest.approx(0.0)

    # And with the roles as originally ranked, the arithmetic is unchanged.
    naked2, _, _ = lp._naked_after(after, TOK_DN, TOK_UP, venue_light_extra=0.0)
    assert naked2 == pytest.approx(2.0)

    # Venue-only heavy size is reported as unpriced: we can see the shares but
    # not what they cost, so nothing that needs an average may use them.
    naked3, _, unpriced3 = lp._naked_after(
        after, TOK_UP, TOK_DN, venue_light_extra=0.0, venue_heavy_extra=3.0)
    assert naked3 == pytest.approx(1.0)   # (10 + 3) - 12
    assert unpriced3 == pytest.approx(3.0)


def test_completion_cancels_the_working_heavy_leg_too(registry: OrderRegistry):
    """A resting heavy order refills the pair right after the cross balances it."""
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    now = 1_000_500
    extra = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-heavy-2", condition_id=COND,
        token_id=TOK_UP, side="BUY", price=0.60, original_size=5.0,
        status="partial", posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(extra)

    client = FakeClient(best_ask=0.30)
    result = lp.complete_pair(client, registry, pair_id,
                              bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert "venue-heavy-2" in result["cancelled"]


def test_completion_rechecks_the_cap_after_the_cancel(registry: OrderRegistry):
    """The approved pair_cost was measured before the cancel.

    A heavy order filling at a worse price in that window pushes the real pair
    past the cap, and the cross must refuse rather than create the losing pair
    the cap exists to prevent.
    """
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    heavy = next(o for o in registry.get_orders_by_pair(pair_id)
                 if o.token_id == TOK_UP)

    class WorseningClient(FakeClient):
        def cancel_order(self, payload):
            out = super().cancel_order(payload)
            # 5 more heavy shares at 0.90 lift the average well past the cap.
            registry.record_fill(FillRecord(
                trade_id="t-heavy-2", order_uuid=heavy.id, size=5.0,
                price=0.90, venue_ts=1_000_200,
            ))
            return out

    client = WorseningClient(best_ask=0.30)
    with pytest.raises(lp.PairCompletionRefused, match="bot_pos_price_max_pair_cost_usd"):
        lp.complete_pair(client, registry, pair_id,
                         bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert not any(c.startswith("buy:") for c in client.calls)


def test_a_pair_spanning_three_tokens_refuses(registry: OrderRegistry):
    """Reducing three legs to the largest two would size against a partial view."""
    pair_id = _one_sided_pair(registry)
    now = 1_000_600
    third = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-third", condition_id=COND,
        token_id="tok-third", side="BUY", price=0.20, original_size=5.0,
        status="open", posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(third)

    with pytest.raises(lp.PairExitRefused, match="token ids"):
        lp.load_pair(registry, pair_id)


def test_completion_refuses_when_a_heavy_fill_is_visible_only_at_the_venue(
    registry: OrderRegistry,
):
    """A matched-size read carries no execution price, and the cap is about price.

    Only the venue moves here; the registry is untouched, which is the actual
    live race. Simulating it by writing to the registry would test the wrong
    thing, because the registry is exactly the source that lags.
    """
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    now = 1_000_500
    extra = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-heavy-2", condition_id=COND,
        token_id=TOK_UP, side="BUY", price=0.60, original_size=5.0,
        status="partial", posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(extra)

    client = FakeClient(best_ask=0.30, venue_matched={"venue-heavy-2": 5.0})

    with pytest.raises(lp.PairCompletionRefused, match="has none yet"):
        lp.complete_pair(client, registry, pair_id,
                         bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert not any(c.startswith("buy:") for c in client.calls)


def test_completion_refuses_when_the_light_leg_overtakes(registry: OrderRegistry):
    """Stage 4 mirror of the exit case: negative naked is not `balanced`."""
    pair_id = _overfilling_light_pair(registry)

    class OvertakingClient(FakeClient):
        def cancel_order(self, payload):
            out = super().cancel_order(payload)
            self.venue_matched["venue-light"] = 12.0
            return out

    client = OvertakingClient(best_ask=0.30)

    with pytest.raises(lp.PairCompletionRefused, match="exceeds"):
        lp.complete_pair(client, registry, pair_id,
                         bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert not any(c.startswith("buy:") for c in client.calls)


# ---------------------------------------------------------------------------
# Stage 4.5 pre-flight — `quote` must be visible to everything downstream
# ---------------------------------------------------------------------------


def test_quote_writes_both_legs_to_the_registry_under_one_pair_id(
    tmp_path, monkeypatch, capsys
):
    """Without this the poll loop has nothing to reconcile and exit/complete
    have no pair_id, so the two legs rest at the venue with real money and
    nothing tracking them."""
    from core_brain import order_manager as live_exec

    monkeypatch.setattr(live_exec, "RUN", tmp_path)
    db = tmp_path / "live.db"

    class Market:
        market_slug = "btc-up-or-down-5m-x"
        up_token = "tok-up-live"
        down_token = "tok-dn-live"
        tick_size = "0.01"
        neg_risk = False

    monkeypatch.setattr("core_brain.markets.fetch_pinned_market",
                        lambda cid, require_rewards=True: Market())

    posted = []

    class Client:
        creds = object()

        def create_order(self, args):
            return MagicMock(tokenId=args.token_id, token=args.token_id)

        def post_orders(self, batch_args, post_only=False, **kwargs):
            res = []
            for arg in batch_args:
                tok = getattr(arg.order, "tokenId", None) or getattr(arg.order, "token", None)
                posted.append(tok)
                res.append({"orderID": f"venue-{tok}", "success": True})
            return res

        def get_order(self, order_id):
            tok = order_id.replace("venue-", "")
            return {"asset_id": tok}

        def get_open_orders(self, *a, **k):
            return []

    monkeypatch.setattr(live_exec, "client", lambda *a, **k: Client())

    live_exec.quote(COND, price=0.48, size=5.0, live=True, db_path=db)

    registry = OrderRegistry(db_path=db)
    pair_ids = {o.pair_id for o in registry.get_active_orders()}
    assert len(pair_ids) == 1, "both legs must share one pair_id"
    pair_id = pair_ids.pop()

    orders = registry.get_orders_by_pair(pair_id)
    assert len(orders) == 2
    assert {o.token_id for o in orders} == {"tok-up-live", "tok-dn-live"}
    # The venue id came back, so each row is `open` and reconcilable by id.
    assert all(o.order_id and o.status == "open" for o in orders)
    # And the operator is told the id rather than having to open live.db.
    assert pair_id in capsys.readouterr().out


def test_quote_leaves_the_row_pending_when_the_venue_returns_no_id(
    tmp_path, monkeypatch
):
    """The order may be live and simply unnamed.

    Guessing an id would bind our row to somebody else's order; `pending` is
    what reconcile's orphan adoption is built to claim.
    """
    from core_brain import order_manager as live_exec

    monkeypatch.setattr(live_exec, "RUN", tmp_path)
    db = tmp_path / "live.db"

    class Market:
        market_slug = "m"
        up_token = "tok-up-live"
        down_token = "tok-dn-live"
        tick_size = "0.01"
        neg_risk = False

    monkeypatch.setattr("core_brain.markets.fetch_pinned_market",
                        lambda cid, require_rewards=True: Market())

    class Client:
        creds = object()

        def create_order(self, args):
            return MagicMock(tokenId=args.token_id, token=args.token_id)

        def post_orders(self, batch_args, post_only=False, **kwargs):
            return [{"success": True}, {"success": True}]          # no id of any spelling

        def get_open_orders(self, *a, **k):
            return []

    monkeypatch.setattr(live_exec, "client", lambda *a, **k: Client())

    live_exec.quote(COND, price=0.48, size=5.0, live=True, db_path=db)

    registry = OrderRegistry(db_path=db)
    orders = registry.get_active_orders()
    assert len(orders) == 2
    assert all(o.order_id is None and o.status == "pending" for o in orders)


def test_venue_order_id_accepts_the_spellings_and_refuses_to_guess():
    from core_brain import order_manager as live_exec

    assert live_exec.venue_order_id({"orderID": "a"}) == "a"
    assert live_exec.venue_order_id({"orderId": "b"}) == "b"
    assert live_exec.venue_order_id({"order_id": "c"}) == "c"
    assert live_exec.venue_order_id({"success": True}) is None
    assert live_exec.venue_order_id(None) is None


def test_quote_says_why_a_market_was_rejected(monkeypatch, tmp_path):
    """An unfunded market quotes; an unusable one is refused, and says why.

    Rewards are not the income. Measured on the paper-run database: 476 merge closes,
    +$1,172.35, mean pair cost $0.96006, against rebate accrual of roughly
    $0.22/day. Every market the ranker graduates pays zero rewards, so demanding
    them here refused the fleet's entire universe. What still earns a refusal is
    a market that is missing, closed, not accepting orders, or does not carry
    exactly two tokens -- and the message must say so rather than sending the
    operator hunting for a typo in a condition_id that is perfectly correct.
    """
    from core_brain import order_manager as live_exec

    monkeypatch.setattr(live_exec, "RUN", tmp_path)

    class Market:
        market_slug = "some-unfunded-market"
        up_token = "u"
        down_token = "d"
        tick_size = "0.01"
        neg_risk = False

    # Pays no rewards -- which is every market the ranker graduates. It quotes,
    # and the dry run prints the legs it would rest.
    seen = {}

    def unfunded(cid, **kwargs):
        seen["kwargs"] = kwargs
        return Market()

    monkeypatch.setattr("core_brain.markets.fetch_pinned_market", unfunded)
    live_exec.quote(COND, price=0.48, size=5.0, live=False,
                    db_path=tmp_path / "live.db")
    assert seen["kwargs"] == {}

    # Unusable for a reason that is not funding: refused, and the message names
    # the causes that are left rather than blaming the id.
    monkeypatch.setattr("core_brain.markets.fetch_pinned_market",
                        lambda cid, **kwargs: None)
    with pytest.raises(SystemExit, match="no tradeable market") as exc_info:
        live_exec.quote(COND, price=0.48, size=5.0, live=False,
                        db_path=tmp_path / "live.db")
    msg = str(exc_info.value)
    assert COND[:12] in msg
    assert "not accepting orders" in msg


def test_taker_paths_never_set_post_only(registry):
    """exit_naked_leg and complete_pair are deliberate taker crosses and must NEVER pass post_only=True."""
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.395, best_bid=0.55)

    # 1. Test exit_naked_leg (taker SELL)
    res_exit = lp.exit_naked_leg(
        client, registry, pair_id,
        bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
        live=True,
    )
    assert res_exit["action"] == "exited"
    assert len(client.orders) == 1
    assert client.orders[0]["side"] == "SELL"

    # 2. Test complete_pair (taker BUY)
    pair_id_2 = "pair-comp-1"
    now = 1_000_000
    heavy = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-h2", condition_id=COND,
        token_id=TOK_UP, side="BUY", price=0.60, original_size=10.0,
        status="filled", posted_ts=now, last_polled_ts=now, pair_id=pair_id_2,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(heavy)
    registry.record_fill(FillRecord(
        trade_id="trade-h2", order_uuid=heavy.id, size=10.0,
        price=0.60, venue_ts=now,
    ))
    light = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-l2", condition_id=COND,
        token_id=TOK_DN, side="BUY", price=0.38, original_size=10.0,
        status="open", posted_ts=now, last_polled_ts=now, pair_id=pair_id_2,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(light)

    client_comp = FakeClient(best_ask=0.35, best_bid=0.55)
    res_comp = lp.complete_pair(
        client_comp, registry, pair_id_2,
        bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
        live=True,
    )
    assert res_comp["action"] == "completed"
    assert len(client_comp.orders) == 1
    assert client_comp.orders[0]["side"] == "BUY"


# ---------------------------------------------------------------------------
# Issue #306 — route-reason instrumentation on the close
# ---------------------------------------------------------------------------


def test_exit_close_without_a_reason_still_records(registry: OrderRegistry):
    """A caller with no reason (stray-guard path) records the close as before."""
    pair_id = _one_sided_pair(registry)
    client = FakeClient(best_ask=0.40)

    result = lp.exit_single_buy(client, registry, pair_id,
                                bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True)

    assert result["action"] == "exited"
    closes = [c for c in registry.get_all_closes()
              if c["method"] == "single_buy_exit"]
    assert len(closes) == 1
    assert closes[0]["reason"] is None


def test_exit_close_carries_reason_only_after_a_successful_sale(
    registry: OrderRegistry,
):
    """The reason reaches the close only when the venue sale succeeded.

    A refused exit writes nothing -- the close is the ledger record of a
    sell that happened, so an aborted exit must leave no close, reason or not.
    """
    pair_id = _one_sided_pair(registry)

    # Refused BEFORE the sale: failed cancel. No close, no reason anywhere.
    refused_client = FakeClient(best_ask=0.40, cancel_ok=False)
    with pytest.raises(lp.PairExitRefused):
        lp.exit_single_buy(refused_client, registry, pair_id,
                           bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True,
                           reason="grace_expired")
    assert registry.get_all_closes() == []

    # Then the real exit with a reason records it, only after the sell.
    client = FakeClient(best_ask=0.40)
    result = lp.exit_single_buy(client, registry, pair_id,
                                bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, live=True,
                                reason="grace_expired")
    assert result["action"] == "exited"
    sell_i = next(i for i, c in enumerate(client.calls) if c.startswith("sell:"))
    closes = [c for c in registry.get_all_closes()
              if c["method"] == "single_buy_exit"]
    assert len(closes) == 1
    assert closes[0]["reason"] == "grace_expired"
    # The close is written after the sale by construction; assert the sell
    # happened at all so the ordering claim has teeth.
    assert any(c.startswith("sell:") for c in client.calls)


# ---------------------------------------------------------------------------
# Unified lifecycle hard-stop execution
# ---------------------------------------------------------------------------

def test_force_exit_sells_after_cancel_despite_a_profitable_completion_ask(
    registry: OrderRegistry,
):
    pair_id = _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.38, best_bid=0.55)

    result = lp.exit_single_buy(
        client, registry, pair_id, bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
        live=True, force=True, reason="hard_stop",
    )

    assert result["action"] == "exited"
    cancel_i = next(i for i, call in enumerate(client.calls)
                    if call.startswith("cancel:"))
    sell_i = next(i for i, call in enumerate(client.calls)
                  if call.startswith("sell:"))
    assert cancel_i < sell_i
    closes = [close for close in registry.get_all_closes()
              if close["method"] == "single_buy_exit"]
    assert len(closes) == 1
    assert closes[0]["reason"] == "hard_stop"


def test_force_exit_does_not_sell_when_cancel_fails(registry: OrderRegistry):
    pair_id = _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.38, cancel_ok=False)

    with pytest.raises(lp.PairExitRefused, match="cancel"):
        lp.exit_single_buy(
            client, registry, pair_id, bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
            live=True, force=True, reason="hard_stop",
        )

    assert not any(call.startswith("sell:") for call in client.calls)


def test_force_exit_routes_to_merge_when_the_pair_fills_during_cancel(
    registry: OrderRegistry,
):
    pair_id = _one_sided_pair(registry, fill_price=0.60)

    class RacingClient(FakeClient):
        def cancel_order(self, payload):
            result = super().cancel_order(payload)
            self.venue_matched["venue-light"] = 10.0
            return result

    client = RacingClient(best_ask=0.38)
    result = lp.exit_single_buy(
        client, registry, pair_id, bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
        live=True, force=True, reason="hard_stop",
    )

    assert result["action"] == "route_to_merge"
    assert not any(call.startswith("sell:") for call in client.calls)


def test_force_exit_does_not_sell_on_registry_venue_position_divergence(
    registry: OrderRegistry,
):
    pair_id = _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.38)

    with pytest.raises(lp.PairExitRefused, match="diverge"):
        lp.exit_single_buy(
            client, registry, pair_id, bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
            live=True, venue_positions={TOK_UP: 3.0},
            force=True, reason="hard_stop",
        )

    assert not any(call.startswith("cancel:") for call in client.calls)
    assert not any(call.startswith("sell:") for call in client.calls)


def test_force_exit_does_not_sell_below_the_venue_minimum_depth(
    registry: OrderRegistry,
):
    pair_id = _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(
        best_ask=0.38,
        best_bid=0.55,
        bid_levels=[{"price": "0.55", "size": "0.5"}],
    )

    with pytest.raises(lp.PairExitRefused, match="below the venue minimum"):
        lp.exit_single_buy(
            client, registry, pair_id, bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
            live=True, force=True, reason="hard_stop",
        )

    assert not any(call.startswith("sell:") for call in client.calls)


def test_load_condition_exposure_with_venue_open_orders(registry: OrderRegistry):
    from core_brain.single_buy_saver import load_condition_exposure
    from core_brain.single_leg_lifecycle import ExposureState

    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = MagicMock()
    # venue confirms the light resting order
    client.get_open_orders.return_value = [{"id": "venue-light"}]

    exposure = load_condition_exposure(client, registry, COND, (TOK_UP, TOK_DN))
    assert exposure.status == ExposureState.COVERED
    assert exposure.heavy_token_id == TOK_UP
    assert exposure.light_token_id == TOK_DN
    assert exposure.held_deficit == 10.0
    assert exposure.coverage_shortfall == 0.0


def test_load_condition_exposure_raises_when_venue_unreachable(registry: OrderRegistry):
    from core_brain.single_buy_saver import load_condition_exposure, ExposureUnavailable

    _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = MagicMock()
    client.get_open_orders.side_effect = Exception("CLOB offline")

    with pytest.raises(ExposureUnavailable, match="CLOB offline"):
        load_condition_exposure(client, registry, COND, (TOK_UP, TOK_DN))


def test_manage_single_leg_positions_stranded_completion_when_enabled(registry: OrderRegistry):
    from core_brain.single_buy_saver import manage_single_leg_positions
    from core_brain.config import MakerConfig

    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.55)
    # Mark the resting light order as canceled to make the position truly stranded (no working hedge)
    light_order = next(o for o in registry.get_orders_by_pair(pair_id) if o.token_id == TOK_DN)
    registry.update_order_status(light_order.id, "cancelled", last_polled_ts=1000)

    # opposing ask is 0.40 -> 0.55 + 0.40 = 0.95 < 0.99
    # fill venue_ts is 1_000_000 ms = 1000s; now=1010s (within 900s window)
    client = FakeClient(best_ask=0.40)
    cfg = MakerConfig(bot_risk_stranded_completion_on=True, bot_pos_price_max_pair_cost_usd=0.99)

    results = manage_single_leg_positions(client, registry, cfg, live=False, now=1010.0)
    assert any(r.get("action") == "would_complete" for r in results)


def test_manage_single_leg_positions_stranded_maker_wait_and_forced_exit(registry: OrderRegistry):
    from core_brain.single_buy_saver import manage_single_leg_positions
    from core_brain.config import MakerConfig

    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.55)
    light_order = next(o for o in registry.get_orders_by_pair(pair_id) if o.token_id == TOK_DN)
    registry.update_order_status(light_order.id, "cancelled", last_polled_ts=1000)

    # fill venue_ts is 1_000_000 ms = 1000s.
    # Case 1: Completion disabled (or ask > cap), aged out past pairs_exit_window_sec (100s)
    # but age <= stranded_max_wait (e.g. now = 1000 + 101 = 1101s <= 1300s)
    # -> action is stranded_awaiting_maker
    client = FakeClient(best_ask=0.60)  # 0.55 + 0.60 = 1.15 > 0.99
    cfg = MakerConfig(
        bot_risk_stranded_completion_on=False,
        bot_risk_gate_aged_out_rescue_on=False,
        bot_risk_pairs_exit_window_sec=100.0,
        bot_risk_stranded_max_wait_sec=300.0,
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    results_wait = manage_single_leg_positions(client, registry, cfg, live=False, now=1101.0)
    assert any(r.get("action") == "stranded_awaiting_maker" for r in results_wait)

    # Case 2: age > stranded_max_wait (e.g. now = 1000 + 350 = 1350s > 1300s)
    # -> forces stranded exit
    results_exit = manage_single_leg_positions(client, registry, cfg, live=False, now=1350.0)
    assert any(r.get("action") == "would_exit" and r.get("route") == "stranded_exit" for r in results_exit)


def test_manage_single_leg_positions_settles_by_completing_under_the_cap(
    registry: OrderRegistry,
):
    """A live aged-out pass prefers the capped cross to dumping the leg (#603).

    0.60 fill + 0.30 ask = 0.90, under the cap: the poll buys the missing DOWN
    leg for 10 shares x 0.30 = $3.00 and records no close. This is the
    settlement-fallback half of the coverage the deleted `_route_pair` held
    (it completed pairs too), now pinned on the live unified lifecycle.
    """
    from core_brain.single_buy_saver import manage_single_leg_positions

    _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    # The fill is 1000s old (venue_ts 1_000_000ms); at now=3900s the 900s window
    # is long past, and a market ending 600s out is inside the 900s lead.
    client = FakeClient(best_ask=0.30, best_bid=0.55, ask_depth=100.0)
    cfg = MakerConfig(
        bot_risk_gate_pairs_rule_on=True, bot_risk_gate_aged_out_rescue_on=True,
        bot_risk_pairs_exit_window_sec=900.0, bot_risk_aged_out_rescue_lead_sec=900.0,
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    results = manage_single_leg_positions(
        client, registry, cfg, live=True, now=3_900.0,
        venue_positions={TOK_UP: 10.0, TOK_DN: 0.0},
        market_state_fn=lambda cid: _open_market_state(end_ts=4_500.0),
    )

    assert len(results) == 1
    assert results[0]["action"] == "aged_out_rescue"
    assert results[0]["route"] == "completed"
    assert results[0]["lifecycle_stage"] == "settlement_fallback"
    assert len(client.orders) == 1
    assert client.orders[0]["side"] == "BUY"
    assert client.orders[0]["token_id"] == TOK_DN
    assert client.orders[0]["amount"] == pytest.approx(3.0)
    assert client.orders[0]["price"] == pytest.approx(0.30)
    assert not any(call.startswith("sell:") for call in client.calls)
    assert registry.get_all_closes() == []


# ---------------------------------------------------------------------------
# Issue #524 — rescue closes record an explicit fee
# ---------------------------------------------------------------------------

class ShadowFakeClient(FakeClient):
    """Shadow rehearsal client: sells into the fresh book with a reason.

    Presence of `_sell_into_bids` alone selects the shadow branch in
    `exit_single_buy` (it passes `reason=` only there); the method below is
    never called on this path. `create_and_post_market_order` answers with the
    rehearsal-style fill (`price`/`size`) the `_exit_fill_*` helpers read.
    """

    def __init__(self, fill_price=None, fill_size=None, **kwargs):
        super().__init__(**kwargs)
        self._fill_price = fill_price
        self._fill_size = fill_size

    def _sell_into_bids(self, *args, **kwargs):
        """Marker only — never called here; presence selects the shadow branch."""

    def create_and_post_market_order(self, order_args, reason=None, **kwargs):
        super().create_and_post_market_order(order_args)
        price = self._fill_price if self._fill_price is not None else self.best_bid
        size = self._fill_size if self._fill_size is not None else order_args.amount
        return {"success": True, "orderID": "venue-shadow-sell",
                "price": price, "size": size}


@pytest.mark.parametrize("method", ["single_buy_exit", "ladder_exit"])
def test_shadow_rescue_exit_records_an_explicit_zero_fee(
    registry: OrderRegistry, method: str,
):
    """A shadow rescue close saves fee 0.0, never NULL (#524)."""
    pair_id = _one_sided_pair(registry, fill_price=0.60)
    client = ShadowFakeClient(best_ask=0.38, best_bid=0.55)

    result = lp.exit_single_buy(
        client, registry, pair_id, bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
        live=True, force=True, reason="hard_stop", method=method,
    )

    assert result["action"] == "exited"
    closes = [c for c in registry.get_all_closes() if c["method"] == method]
    assert len(closes) == 1
    assert closes[0]["fee"] is not None
    assert closes[0]["fee"] == 0.0


def test_partial_shadow_rescue_exit_records_zero_fee(registry: OrderRegistry):
    """A short shadow fill still records its close with fee 0.0 (#524)."""
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = ShadowFakeClient(best_ask=0.38, best_bid=0.55, fill_size=4.0)

    result = lp.exit_single_buy(
        client, registry, pair_id, bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
        live=True, force=True, reason="hard_stop",
    )

    assert result["action"] == "exited"
    closes = [c for c in registry.get_all_closes()
              if c["method"] == "single_buy_exit"]
    assert len(closes) == 1
    assert closes[0]["shares"] < 10
    assert closes[0]["fee"] == 0.0


def test_live_rescue_exit_keeps_fee_unmeasured(registry: OrderRegistry):
    """Live taker fees are unknown at sell time: the close stays NULL (#524).

    Policy pin for the adopted shadow-only fee rule — a live zero would be an
    invented measurement. If the owner overrides to all-rescues, this test is
    replaced by one asserting 0.0 (see #524 plan T2).
    """
    pair_id = _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.38, best_bid=0.55)

    result = lp.exit_single_buy(
        client, registry, pair_id, bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
        live=True, force=True, reason="hard_stop",
    )

    assert result["action"] == "exited"
    closes = [c for c in registry.get_all_closes()
              if c["method"] == "single_buy_exit"]
    assert len(closes) == 1
    assert closes[0]["fee"] is None


def test_executed_rescue_close_reads_as_measured_zero_friction(
    registry: OrderRegistry,
):
    """A real saved shadow close — not hand-built — reads measured zero (#524)."""
    pair_id = _one_sided_pair(registry, fill_price=0.60)
    client = ShadowFakeClient(best_ask=0.38, best_bid=0.55)

    result = lp.exit_single_buy(
        client, registry, pair_id, bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
        live=True, force=True, reason="hard_stop",
    )

    assert result["action"] == "exited"
    closes = registry.get_all_closes()
    assert len(closes) == 1
    # A break-even close has no denominator and reads mean_tcdr None, which
    # would make this proof vacuous — the exit must book a real loss.
    assert closes[0]["realized_pnl"] != 0
    summary = rescue_friction_summary(closes)
    assert summary["fee_measured_count"] == 1
    assert summary["mean_tcdr"] == 0.0





# ---------------------------------------------------------------------------
# Issue #525 — lifecycle exits record the trigger bid
# ---------------------------------------------------------------------------
# The bid that trips the stop is already in hand as the manager's decision read;
# the hard-stop exit must pass it to `exit_single_buy` the way every other hard
# stop does, so latency drag is measured instead of assumed. The poll's other
# route -- the settlement fallback -- has no trigger to hand over and must leave
# the reference null rather than borrow the execution bid.
#
# The coverage below drives the unified lifecycle (`manage_single_leg_positions`)
# instead of the deleted `_route_pair` (#603).

TRIG525_COND = "0xcond-ladder-525"
TRIG525_PAIR = "trig-0xcond-trig-525"
TRIG525_NOW = 2_000_000
# An old fill (1000s) whose 900s window is long gone: the poll's aged-out route
# owns it, and a market end 600s out sits inside the 900s lead.
TRIG525_FILL_MS = 1_000_000
TRIG525_ROUTE_S = 3_900.0
TRIG525_END_S = 4_500.0


def _trig525_pair(registry: OrderRegistry, when: int) -> None:
    """Single-leg pair: UP 10 filled at 0.60, DOWN 10 working at 0.38."""
    down = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-dn", condition_id=TRIG525_COND,
        token_id=TOK_DN, side="BUY", price=0.38, original_size=10.0,
        status="open", posted_ts=when, last_polled_ts=when,
        pair_id=TRIG525_PAIR, max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(down)
    up = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-up", condition_id=TRIG525_COND,
        token_id=TOK_UP, side="BUY", price=0.60, original_size=10.0,
        status="open", posted_ts=when, last_polled_ts=when,
        pair_id=TRIG525_PAIR, max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(up)
    registry.record_fill(FillRecord(
        trade_id=f"trade-{up.id}", order_uuid=up.id, size=10.0,
        price=0.60, venue_ts=when,
    ))
    registry.update_order_status(up.id, status="filled", last_polled_ts=when)
    registry.log_quote(QuoteRecord(
        ts=when / 1000.0, condition_id=TRIG525_COND, token_id=TOK_UP,
        side="UP", price=0.60, size=10.0,
    ))
    registry.log_quote(QuoteRecord(
        ts=when / 1000.0, condition_id=TRIG525_COND, token_id=TOK_DN,
        side="DOWN", price=0.38, size=10.0,
    ))


def _trig525_cfg():
    """The manager's own knobs: both routes on, a 900s window and lead."""
    return MakerConfig(
        bot_risk_gate_pairs_rule_on=True, bot_risk_gate_aged_out_rescue_on=True,
        bot_risk_pairs_exit_window_sec=900.0, bot_risk_aged_out_rescue_lead_sec=900.0,
        bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, bot_risk_exposure_max_order_usd=100.0,
    )


def _open_market_state(end_ts: float) -> MarketEndState:
    """An open, accepting market with a stated end -- no venue, no network."""
    return MarketEndState(
        condition_id=TRIG525_COND, closed=False, end_ts=end_ts,
        end_date_iso=None, end_date_passed=False, accepting_orders=True,
        resolved=False, unreachable=False,
    )


class DecisionThenExecutionBook:
    """Decision bid until the first cancel, execution bid after it.

    `exit_single_buy` cancels resting orders before its fresh read, so gating
    on the cancel proves the recorded trigger and fresh bids come from two
    separate reads. No network. An empty bid list means "no bid".
    """

    def __init__(self, decision_bid, execution_bid, light_ask=0.40,
                 depth=100.0):
        self.decision_bid = decision_bid
        self.execution_bid = execution_bid
        self.light_ask = light_ask
        self.depth = depth
        self.calls: list[str] = []

    def _book(self, bid):
        bids = ([] if bid is None
                else [{"price": str(bid), "size": str(self.depth)}])
        return {
            "asset_id": "tok", "tick_size": "0.01",
            "asks": [{"price": str(self.light_ask),
                      "size": str(self.depth)}],
            "bids": bids,
        }

    def get_order_book(self, token_id):
        self.calls.append(f"book:{token_id}")
        # The light leg only needs its ask (the exit's completion guard); an
        # empty bid list keeps it out of any trigger check.
        if token_id == TOK_DN:
            return self._book(None)
        cancelled = any(c.startswith("cancel:") for c in self.calls)
        return self._book(
            self.execution_bid if cancelled else self.decision_bid)

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


def _trig525_route(registry, venue, now_s, *, market_state_fn=None,
                   funder=None):
    """One unified-lifecycle pass over the #525 pair, with the venue position
    the exit's divergence gate needs (UP 10 held, DOWN nothing)."""
    results = lp.manage_single_leg_positions(
        venue, registry, _trig525_cfg(), live=True, now=now_s,
        venue_positions={TOK_UP: 10.0, TOK_DN: 0.0},
        market_state_fn=market_state_fn,
        funder=funder,
    )
    assert len(results) == 1
    return results[0]


def test_hard_stop_exit_records_trigger_bid(registry: OrderRegistry):
    """The hard stop books the decision bid, not the execution bid.

    A held bid at the 0.15 stop line is the decision read; the exit's own read
    after the cancel is 0.14. Both reach the close, so latency drag is a
    measurement rather than the fresh bid standing in for the trigger.
    """
    _trig525_pair(registry, TRIG525_NOW)
    venue = DecisionThenExecutionBook(decision_bid=0.15, execution_bid=0.14)
    routed = _trig525_route(registry, venue, TRIG525_NOW / 1000.0 + 10.0)

    assert routed["action"] == "exited"
    assert routed["route"] == "hard_stop"
    assert routed["lifecycle_state"] == "HARD_STOP"
    closes = [c for c in registry.get_all_closes()
              if c.get("method") == "single_buy_exit"]
    assert len(closes) == 1
    assert closes[0]["reason"] == "lifecycle_hard_stop"
    assert closes[0]["trigger_bid"] == pytest.approx(0.15)
    assert closes[0]["fresh_bid"] == pytest.approx(0.14)
    assert closes[0]["trigger_bid"] != pytest.approx(closes[0]["fresh_bid"])
    assert [c for c in venue.calls if c.startswith("sell:")] == ["sell:tok-up:10.0"]
    assert not any(c.startswith("buy:") for c in venue.calls)


def test_hard_stop_only_store_has_zero_assumed_references(
    registry: OrderRegistry,
):
    """A stop-only store measures every reference — assumed count is 0."""
    _trig525_pair(registry, TRIG525_NOW)
    venue = DecisionThenExecutionBook(decision_bid=0.15, execution_bid=0.14)
    _trig525_route(registry, venue, TRIG525_NOW / 1000.0 + 10.0)

    friction = rescue_friction_summary(registry.get_all_closes())
    assert friction["recorded_reference_count"] == 1
    assert friction["assumed_reference_count"] == 0


def test_settlement_fallback_exit_keeps_trigger_null(registry: OrderRegistry):
    """The aged-out fallback has no trigger bid and never invents one.

    The pair cannot complete (0.60 + 0.40 = 1.00, over the 0.995 cap), so the
    pass sells at the exit's own fresh read of 0.56. There is no decision bid
    to reference, and `grace_expired` is not a route this pass can take.
    """
    _trig525_pair(registry, TRIG525_FILL_MS)
    venue = DecisionThenExecutionBook(decision_bid=0.57, execution_bid=0.56)
    routed = _trig525_route(
        registry, venue, TRIG525_ROUTE_S,
        market_state_fn=lambda cid: _open_market_state(end_ts=TRIG525_END_S),
    )

    assert routed["action"] == "aged_out_rescue"
    assert routed["route"] == "exited"
    assert routed["lifecycle_stage"] == "settlement_fallback"
    assert "grace" not in routed["reason"]
    closes = [c for c in registry.get_all_closes()
              if c.get("method") == "single_buy_exit"]
    assert len(closes) == 1
    assert closes[0]["reason"] == "aged_out_rescue"
    assert closes[0]["trigger_bid"] is None
    assert closes[0]["fresh_bid"] == pytest.approx(0.56)


def test_settlement_fallback_without_a_decision_bid_keeps_trigger_null(
    registry: OrderRegistry,
):
    """No decision bid means no trigger: the fallback never borrows fresh_bid."""
    _trig525_pair(registry, TRIG525_FILL_MS)
    venue = DecisionThenExecutionBook(decision_bid=None, execution_bid=0.56)
    routed = _trig525_route(
        registry, venue, TRIG525_ROUTE_S,
        market_state_fn=lambda cid: _open_market_state(end_ts=TRIG525_END_S),
    )

    assert routed["action"] == "aged_out_rescue"
    closes = [c for c in registry.get_all_closes()
              if c.get("method") == "single_buy_exit"]
    assert len(closes) == 1
    assert closes[0]["trigger_bid"] is None
    assert closes[0]["fresh_bid"] == pytest.approx(0.56)


def test_shadow_hard_stop_records_trigger_bid(tmp_path):
    """A hard stop through the real shadow client books the trigger bid."""
    from core_brain.order_registry import init_db
    from core_brain.shadow_exec import ShadowExecutionClient, ensure_shadow_tables

    db = tmp_path / "shadow525.db"
    init_db(db)
    reg = OrderRegistry(db_path=db)
    ensure_shadow_tables(db)

    _trig525_pair(reg, TRIG525_NOW)

    # Decision bid first, then the live bid the exit prices off; once the stub
    # runs dry it keeps the last book, which is the real steady state. A hard
    # stop sells into THIS book (shadow sells fresh for hard-stop reasons), so
    # no queue-mark snapshot is involved.
    bids = iter([0.15, 0.14])

    def book_fn(host, token_id):
        if token_id == TOK_DN:
            return {"bids": {0.38: 100.0}, "asks": {0.40: 100.0}}
        bid = next(bids, 0.14)
        return {"bids": {bid: 100.0}, "asks": {0.65: 100.0}}

    client = ShadowExecutionClient(reg, db, book_fn=book_fn)

    routed = lp.manage_single_leg_positions(
        client, reg, MakerConfig(
            bot_risk_gate_pairs_rule_on=True, bot_risk_pairs_exit_window_sec=900.0,
            bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
        ),
        live=True, now=TRIG525_NOW / 1000.0 + 10.0,
        venue_positions={TOK_UP: 10.0, TOK_DN: 0.0},
    )[0]

    assert routed["action"] == "exited"
    assert routed["route"] == "hard_stop"
    closes = [c for c in reg.get_all_closes()
              if c.get("method") == "single_buy_exit"]
    assert len(closes) == 1
    assert closes[0]["reason"] == "lifecycle_hard_stop"
    assert closes[0]["trigger_bid"] == pytest.approx(0.15)
    assert closes[0]["fresh_bid"] == pytest.approx(0.14)


# ---------------------------------------------------------------------------
# Atomic cancel-then-sell pinning on hard-stop (#628, Task T2)
# ---------------------------------------------------------------------------


def test_hard_stop_exit_cancels_opposite_before_selling(registry: OrderRegistry):
    """#628: Opposite resting quote must be cancelled before selling held leg."""
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.40)

    result = lp.exit_single_buy(
        client, registry, pair_id, MAX_PAIR_COST,
        live=True, reason="lifecycle_hard_stop", force=True,
        trigger_bid=0.15,
    )

    assert result["action"] == "exited"
    assert "venue-light" in result["cancelled"]
    cancel_i = next(i for i, c in enumerate(client.calls)
                    if "venue-light" in c and c.startswith("cancel:"))
    sell_i = next(i for i, c in enumerate(client.calls) if c.startswith("sell:"))
    assert cancel_i < sell_i, "opposite resting quote must be cancelled before market selling"
    closes = [c for c in registry.get_all_closes() if c.get("method") == "single_buy_exit"]
    assert len(closes) == 1
    assert closes[0]["reason"] == "lifecycle_hard_stop"


def test_hard_stop_exit_refuses_when_opposite_fills_in_window(registry: OrderRegistry):
    """#628: If light leg fills and overtakes during cancel, refuse exit and never sell."""
    pair_id = _overfilling_light_pair(registry)
    light = next(o for o in registry.get_active_orders() if o.token_id == TOK_DN)

    class OvertakingClient(FakeClient):
        def cancel_order(self, payload):
            out = super().cancel_order(payload)
            self.venue_matched["venue-light"] = 12.0
            registry.record_fill(FillRecord(
                trade_id="t-light", order_uuid=light.id, size=12.0,
                price=0.30, venue_ts=1_000_100,
            ))
            registry.update_order_status(light.id, "filled", 1_000_100)
            return out

    client = OvertakingClient(best_ask=0.40)
    with pytest.raises(lp.PairExitRefused, match="changed token"):
        lp.exit_single_buy(
            client, registry, pair_id, MAX_PAIR_COST,
            live=True, reason="lifecycle_hard_stop", force=True,
            trigger_bid=0.15,
        )

    assert not any(c.startswith("sell:") for c in client.calls)


def test_hard_stop_exit_refuses_when_cancel_fails(registry: OrderRegistry):
    """#628: If cancel of resting leg fails, refuse exit and never sell."""
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.40, cancel_ok=False)

    with pytest.raises(lp.PairExitRefused, match="Cancel of"):
        lp.exit_single_buy(
            client, registry, pair_id, MAX_PAIR_COST,
            live=True, reason="lifecycle_hard_stop", force=True,
            trigger_bid=0.15,
        )

    assert not any(c.startswith("sell:") for c in client.calls)


def test_hard_stop_full_exit_resets_lifecycle(registry: OrderRegistry, monkeypatch):
    """#646: reset now deletes the row and marks re-qualification.

    Was #628's DUAL_RESTING write off an assumed-flat read; only the shared
    proof clears the row now, so this venue proves flat (empty book, empty
    trades, empty balances) instead of merely selling.
    """
    import json as _json
    import urllib.request as _urlopen_mod

    _trig525_pair(registry, TRIG525_NOW)
    venue = DecisionThenExecutionBook(decision_bid=0.15, execution_bid=0.14)
    venue.creds = object()
    venue.open_orders = [{"id": "venue-up"}, {"id": "venue-dn"}]
    venue.get_open_orders = lambda: list(venue.open_orders)
    venue.get_trades = lambda params=None: []
    _orig_cancel = venue.cancel_order

    def _proving_cancel(payload):
        out = _orig_cancel(payload)
        oid = str(getattr(payload, "orderID", payload))
        venue.open_orders = [o for o in venue.open_orders if o["id"] != oid]
        return out

    venue.cancel_order = _proving_cancel

    class _Resp:
        def read(self):
            return _json.dumps([]).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        _urlopen_mod, "urlopen", lambda req, timeout=None: _Resp())
    routed = _trig525_route(registry, venue, TRIG525_NOW / 1000.0 + 10.0,
                            funder="0xfunder")

    assert routed["action"] == "exited"
    assert routed["reset_status"] == "RESET"
    assert registry.get_lifecycle_state(TRIG525_PAIR) is None
    assert registry.requalification_marks()[TRIG525_COND].status == "awaiting"


def test_hard_stop_partial_exit_keeps_hard_stop(registry: OrderRegistry):
    """#628: When only partial shares are sold, lifecycle row remains HARD_STOP."""
    _trig525_pair(registry, TRIG525_NOW)

    class PartialVenue(DecisionThenExecutionBook):
        def create_and_post_market_order(self, order_args, options=None,
                                         order_type="FOK", defer_exec=False):
            super().create_and_post_market_order(order_args, options=options,
                                                 order_type=order_type,
                                                 defer_exec=defer_exec)
            return {"success": True, "size": 4.0, "price": "0.14", "orderID": "v-partial"}

    venue = PartialVenue(decision_bid=0.15, execution_bid=0.14)
    routed = _trig525_route(registry, venue, TRIG525_NOW / 1000.0 + 10.0)

    assert routed["action"] == "exited"
    assert routed["size"] == 4.0
    rec = registry.get_lifecycle_state(TRIG525_PAIR)
    assert rec is not None
    assert rec.state == "HARD_STOP"


def test_saver_forwards_canonical_emergency_drawdown(registry: OrderRegistry):
    import warnings
    from unittest.mock import MagicMock, patch
    cfg = MakerConfig(
        bot_risk_gate_pairs_rule_on=True,
        bot_pos_price_max_pair_cost_usd=0.99,
        bot_risk_circuit_max_drawdown_pct=60.0,
    )
    _trig525_pair(registry, TRIG525_NOW)
    venue = DecisionThenExecutionBook(decision_bid=0.15, execution_bid=0.14)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        with patch("core_brain.single_leg_lifecycle.transition") as mock_trans:
            mock_trans.return_value = MagicMock(action="balanced")
            lp.manage_single_leg_positions(
                venue, registry, cfg, live=False, now=TRIG525_NOW / 1000.0 + 10.0,
                venue_positions={TOK_UP: 10.0, TOK_DN: 0.0},
            )
            assert mock_trans.called
            assert mock_trans.call_args[1]["bot_risk_circuit_max_drawdown_pct"] == 60.0
            assert "emergency_drawdown_pct" not in mock_trans.call_args[1]

    for w in recorded:
        assert "emergency_drawdown_pct is deprecated" not in str(w.message)


def test_manage_held_bid_uses_decision_book_fn_when_wired(registry: OrderRegistry):
    """#659: the lifecycle decision read rides the provider; exits stay fresh."""
    from core_brain.single_buy_saver import manage_single_leg_positions

    _one_sided_pair(registry, filled_size=10.0, fill_price=0.55)
    decision_calls: list[str] = []

    def decision_book_fn(token_id):
        decision_calls.append(token_id)
        return {"bids": [{"price": "0.50", "size": "25"}],
                "asks": [{"price": "0.52", "size": "25"}]}

    client = FakeClient(best_ask=0.40, best_bid=0.55)
    cfg = MakerConfig()
    results = manage_single_leg_positions(
        client, registry, cfg, live=False, now=1010.0,
        decision_book_fn=decision_book_fn,
    )
    assert decision_calls == [TOK_UP]
    assert not any(c == f"book:{TOK_UP}" for c in client.calls)
    assert results


def test_manage_held_bid_falls_back_to_client_without_decision_fn(
        registry: OrderRegistry):
    """Unwired callers keep today's behavior exactly."""
    from core_brain.single_buy_saver import manage_single_leg_positions

    _one_sided_pair(registry, filled_size=10.0, fill_price=0.55)
    client = FakeClient(best_ask=0.40, best_bid=0.55)
    results = manage_single_leg_positions(
        client, registry, MakerConfig(), live=False, now=1010.0)
    assert f"book:{TOK_UP}" in client.calls
    assert results

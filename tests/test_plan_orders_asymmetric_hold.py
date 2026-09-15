"""A resting BUY must not be cancelled by the market arriving at it.

`plan_orders` re-quotes an order whose desired price has left the dead band,
and until now it did that in BOTH directions. For a resting BUY the two
directions are not the same event:

  * the target rises -- the book walked away, our bid is stranded below the
    market and will never be reached. Re-quote.
  * the target FALLS -- the book is walking down onto our bid. A limit BUY at
    0.47 fills at 0.47 when a seller sweeps through it; that is the whole
    mechanism. Cancelling here removes the order at the exact moment it was
    about to fill, and the replacement goes to the back of a new level lower
    down, where the same thing happens again.

Measured on shadow-01 over the 17:06-21:00 window of 2026-09-15: of 755
`price_moved` cancels, 393 (52%) fired while the best bid was falling toward
the order, at a median order lifetime of 42s. The run booked zero fills across
1,157 orders.

The hold is capped, because a large drop is the market LEAVING, not arriving:
holding a bid 36c above the book is an adverse fill, not a queue position.
"""

from __future__ import annotations

from core_brain.config import MakerConfig
from core_brain.quotes import QuoteIntent
from core_brain.trader_loop import CANCEL_PRICE_MOVED, plan_orders


def _intent(side="UP", token="tok-up", price=0.60, size=5):
    i = QuoteIntent(side=side, token_id=token, price=price, size=size,
                    mid=price + 0.01, edge_vs_mid=0.01)
    i.pair_id = None
    return i


def _open(token="tok-up", price=0.60, oid="o1"):
    return {"token_id": token, "price": price, "order_id": oid,
            "side": "BUY", "status": "open"}


def _cfg():
    return MakerConfig(max_completable_pair_cost=1.00)


class TestAsymmetricRequoteHold:
    def test_a_bid_is_held_when_the_target_falls_within_the_cap(self):
        # Target 0.56 sits 4c below the resting 0.60 -- outside the 3c band, so
        # the symmetric rule re-quotes. The market is coming to the order.
        to_cancel, to_submit = plan_orders(
            [_open(price=0.60)], [_intent(price=0.56)],
            dead_band=0.03, cfg=_cfg(), hedge_asks={"tok-up": 0.35},
            hold_below_target=0.05)
        assert to_cancel == []
        # A held order rests outside the tolerance, so the token must still be
        # reported as covered -- otherwise a second leg is posted beside it.
        assert to_submit == []

    def test_a_bid_is_cancelled_when_the_target_rises(self):
        # The asymmetry itself. Same 4c move, opposite sign: the book walked
        # away and 0.60 is now stranded under the market.
        to_cancel, to_submit = plan_orders(
            [_open(price=0.60)], [_intent(price=0.64)],
            dead_band=0.03, cfg=_cfg(), hedge_asks={"tok-up": 0.30},
            hold_below_target=0.05)
        assert [o["order_id"] for o in to_cancel] == ["o1"]
        assert [i.price for i in to_submit] == [0.64]

    def test_a_bid_is_cancelled_when_the_target_falls_past_the_cap(self):
        # A 12c drop is the market leaving. Holding a bid that far above the
        # book buys an adverse fill, not a queue position.
        to_cancel, _ = plan_orders(
            [_open(price=0.60)], [_intent(price=0.48)],
            dead_band=0.03, cfg=_cfg(), hedge_asks={"tok-up": 0.35},
            hold_below_target=0.05)
        assert [o["order_id"] for o in to_cancel] == ["o1"]

    def test_the_pair_cost_regate_still_cancels_a_held_bid(self):
        # Direction never overrides the money gate: 0.60 + a 0.42 hedge ask is
        # a 1.02 completable pair, which is a booked loss however good the
        # queue position is.
        to_cancel, _ = plan_orders(
            [_open(price=0.60)], [_intent(price=0.57)],
            dead_band=0.01, cfg=_cfg(), hedge_asks={"tok-up": 0.42},
            hold_below_target=0.05)
        assert [o["order_id"] for o in to_cancel] == ["o1"]

    def test_the_hold_needs_a_real_hedge_ask(self):
        # `completable_pair_block` reads a missing ask as NO OPINION, and the
        # hold must not read that as "passed" -- with nothing checking the
        # economics of the stale price, holding is an unmeasured bet.
        to_cancel, _ = plan_orders(
            [_open(price=0.60)], [_intent(price=0.56)],
            dead_band=0.03, cfg=_cfg(), hedge_asks={"tok-up": None},
            hold_below_target=0.05)
        assert [o["order_id"] for o in to_cancel] == ["o1"]

    def test_the_hold_is_off_when_the_cap_is_zero(self):
        # Callers that never pass the cap keep the symmetric rule exactly.
        to_cancel, _ = plan_orders(
            [_open(price=0.60)], [_intent(price=0.56)],
            dead_band=0.03, cfg=_cfg(), hedge_asks={"tok-up": 0.35})
        assert [o["order_id"] for o in to_cancel] == ["o1"]

    def test_a_cancelled_downward_move_still_records_its_reason(self):
        # A cancel with no recorded reason cannot be told from churn later.
        reasons: dict = {}
        plan_orders(
            [_open(price=0.60)], [_intent(price=0.48)],
            dead_band=0.03, cfg=_cfg(), hedge_asks={"tok-up": 0.35},
            hold_below_target=0.05, reasons=reasons)
        assert reasons == {"o1": CANCEL_PRICE_MOVED}

    def test_the_queue_hold_and_the_direction_hold_are_independent(self):
        # An order deep in the queue is still held on a downward move: the
        # limit fills at its own price whoever is in front of it.
        to_cancel, _ = plan_orders(
            [_open(price=0.60)], [_intent(price=0.56)],
            dead_band=0.03, cfg=_cfg(), hedge_asks={"tok-up": 0.35},
            queue_ahead={"o1": 40000.0}, hold_queue_shares=10.0,
            hold_below_target=0.05)
        assert to_cancel == []


class TestConfigWiring:
    def test_the_cap_ships_enabled(self):
        # Shipping at 0.0 would leave the measured defect in place.
        assert MakerConfig().requote_hold_below_target > 0.0

    def test_the_cap_is_at_least_the_dead_band(self):
        # A cap under the band can never fire: an order inside the band was
        # already kept by the tolerance.
        cfg = MakerConfig()
        assert cfg.requote_hold_below_target >= cfg.requote_dead_band


class TestCancelReportNamesBothHolds:
    def test_the_report_names_the_direction_hold_too(self):
        # An operator reading "only the queue hold can keep these" after the
        # direction hold shipped is being told a rule does not exist.
        from core_brain.cancel_report import format_report

        text = format_report({
            "readable": True, "db_path": "x.db", "cancelled": 10,
            "unattributed": 0,
            "reasons": {"price_moved": {"count": 10, "median_queue_ahead": 5.0,
                                        "min_queue_ahead": 1.0}},
        })
        assert "requote_hold_below_target" in text
        assert "requote_hold_queue_shares" in text
        assert "direction on within 0.05" in text

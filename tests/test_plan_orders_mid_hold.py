"""A resting order holds when the mid drops onto it instead of cancelling.

`plan_orders` cancels a token with no intent this cycle (`not_quoted`), and
until now it did that no matter how close the mid was. When the book walks
DOWN onto our bid, cancelling hands back the queue position at the exact
moment the order was about to fill: a limit BUY at 0.48 fills at 0.48 when
a seller sweeps through it.

The mid-hold band (#419): while `mid <= order.price + 0.02` the order is
HELD -- no cancel, no re-quote, no duplicate submit. Safety routes
(terminal refusals, hard stop, lifecycle cancels, explicit cancel sets)
still cancel in band, and a missing or unusable mid changes nothing.
"""

from __future__ import annotations

from core_brain.config import MakerConfig
from core_brain.quotes import QuoteIntent
from core_brain.trader_loop import (
    CANCEL_NOT_QUOTED, VisitOutcome, plan_orders,
)


def _intent(side="UP", token="tok-up", price=0.60, size=5):
    i = QuoteIntent(side=side, token_id=token, price=price, size=size,
                    mid=price + 0.01, edge_vs_mid=0.01)
    i.pair_id = None
    return i


def _open(token="tok-up", price=0.48, oid="o-up"):
    return {"token_id": token, "price": price, "order_id": oid,
            "side": "BUY", "status": "open"}


def _down_open(oid="o-dn"):
    return {"token_id": "tok-dn", "price": 0.52, "order_id": oid,
            "side": "BUY", "status": "open"}


def _cfg():
    return MakerConfig(max_completable_pair_cost=1.00)


class TestMidHoldBand:
    def test_equality_holds_without_cancel_or_submit(self):
        # Mid exactly at price + band: 0.50 == 0.48 + 0.02. Only a DOWN
        # intent this cycle, so UP has no target -- and must still hold.
        to_cancel, to_submit = plan_orders(
            [_open(), _down_open()],
            [_intent(side="DOWN", token="tok-dn", price=0.52)],
            cfg=_cfg(), token_mids={"tok-up": 0.50, "tok-dn": 0.51})
        assert to_cancel == []
        assert [i.token_id for i in to_submit] == []

    def test_in_band_holds(self):
        to_cancel, to_submit = plan_orders(
            [_open(), _down_open()],
            [_intent(side="DOWN", token="tok-dn", price=0.52)],
            cfg=_cfg(), token_mids={"tok-up": 0.46, "tok-dn": 0.51})
        assert to_cancel == []
        assert [i.token_id for i in to_submit] == []

    def test_in_band_hold_records_no_not_quoted_reason(self):
        reasons = {}
        plan_orders(
            [_open(), _down_open()],
            [_intent(side="DOWN", token="tok-dn", price=0.52)],
            cfg=_cfg(), reasons=reasons,
            token_mids={"tok-up": 0.46, "tok-dn": 0.51})
        assert reasons.get("o-up") is None

    def test_out_of_band_cancels_not_quoted(self):
        reasons = {}
        to_cancel, _ = plan_orders(
            [_open(), _down_open()],
            [_intent(side="DOWN", token="tok-dn", price=0.52)],
            cfg=_cfg(), reasons=reasons,
            token_mids={"tok-up": 0.501, "tok-dn": 0.51})
        assert [o["order_id"] for o in to_cancel] == ["o-up"]
        assert reasons.get("o-up") == CANCEL_NOT_QUOTED

    def test_missing_midpoint_cancels_as_today(self):
        to_cancel, _ = plan_orders(
            [_open(), _down_open()],
            [_intent(side="DOWN", token="tok-dn", price=0.52)],
            cfg=_cfg(), token_mids={"tok-dn": 0.51})
        assert [o["order_id"] for o in to_cancel] == ["o-up"]

    def test_no_mapping_cancels_as_today(self):
        to_cancel, _ = plan_orders(
            [_open(), _down_open()],
            [_intent(side="DOWN", token="tok-dn", price=0.52)],
            cfg=_cfg(), token_mids=None)
        assert [o["order_id"] for o in to_cancel] == ["o-up"]

    def test_terminal_refusal_cancels_even_in_band(self):
        to_cancel, _ = plan_orders(
            [_open(), _down_open()], [],
            cfg=_cfg(), visit_outcome=VisitOutcome.REFUSED_TERMINAL,
            token_mids={"tok-up": 0.50, "tok-dn": 0.51})
        assert {o["order_id"] for o in to_cancel} == {"o-up", "o-dn"}

    def test_grace_expiry_holds_in_band(self):
        to_cancel, to_submit = plan_orders(
            [_open(), _down_open()], [],
            cfg=_cfg(), visit_outcome=VisitOutcome.REFUSED_GRACE_EXPIRED,
            token_mids={"tok-up": 0.50, "tok-dn": 0.51})
        assert to_cancel == []
        assert to_submit == []

    def test_grace_expiry_cancels_out_of_band(self):
        to_cancel, _ = plan_orders(
            [_open(), _down_open()], [],
            cfg=_cfg(), visit_outcome=VisitOutcome.REFUSED_GRACE_EXPIRED,
            token_mids={"tok-up": 0.55, "tok-dn": 0.51})
        assert [o["order_id"] for o in to_cancel] == ["o-up"]

    def test_drifted_intent_neither_cancels_nor_submits(self):
        # Wanted token, large drift, mid near our price: NO RE-CHECK keeps
        # the order and the held token suppresses the replacement submit.
        to_cancel, to_submit = plan_orders(
            [_open(), _down_open()],
            [_intent(price=0.40),
             _intent(side="DOWN", token="tok-dn", price=0.52)],
            cfg=_cfg(), token_mids={"tok-up": 0.46, "tok-dn": 0.51})
        assert to_cancel == []
        assert to_submit == []

def _down_hedge(price=0.48, oid="o-dn-hedge"):
    return {"token_id": "tok-dn", "price": price, "order_id": oid,
            "side": "BUY", "status": "open"}


def _pair_intent(price=0.51, pair_id="pair-1"):
    i = _intent(side="DOWN", token="tok-dn", price=price)
    i.pair_id = pair_id
    return i


class TestMidHoldLifecycleReplace:
    def test_in_band_replace_holds_without_cancel_or_submit(self):
        to_cancel, to_submit = plan_orders(
            [_down_hedge()], [_pair_intent()],
            cfg=_cfg(), replace_order_ids={"o-dn-hedge"},
            lifecycle_pair_id="pair-1",
            token_mids={"tok-dn": 0.49})
        assert to_cancel == []
        assert to_submit == []

    def test_out_of_band_replace_proceeds(self):
        to_cancel, to_submit = plan_orders(
            [_down_hedge()], [_pair_intent()],
            cfg=_cfg(), replace_order_ids={"o-dn-hedge"},
            lifecycle_pair_id="pair-1",
            token_mids={"tok-dn": 0.51})
        assert [o["order_id"] for o in to_cancel] == ["o-dn-hedge"]
        assert [i.price for i in to_submit] == [0.51]

    def test_missing_midpoint_replaces_as_today(self):
        to_cancel, to_submit = plan_orders(
            [_down_hedge()], [_pair_intent()],
            cfg=_cfg(), replace_order_ids={"o-dn-hedge"},
            lifecycle_pair_id="pair-1", token_mids=None)
        assert [o["order_id"] for o in to_cancel] == ["o-dn-hedge"]
        assert [i.price for i in to_submit] == [0.51]

    def test_cancel_wins_over_replace_in_band(self):
        reasons = {}
        to_cancel, _ = plan_orders(
            [_down_hedge()], [_pair_intent()],
            cfg=_cfg(), reasons=reasons,
            replace_order_ids={"o-dn-hedge"},
            cancel_order_ids={"o-dn-hedge"},
            lifecycle_pair_id="pair-1",
            token_mids={"tok-dn": 0.49})
        assert [o["order_id"] for o in to_cancel] == ["o-dn-hedge"]
        assert reasons.get("o-dn-hedge") == "lifecycle_replace"

    def test_lifecycle_cancel_fires_in_band(self):
        reasons = {}
        to_cancel, _ = plan_orders(
            [_open(), _down_open()], [],
            cfg=_cfg(), reasons=reasons,
            cancel_order_ids={"o-up"},
            token_mids={"tok-up": 0.46, "tok-dn": 0.51})
        assert [o["order_id"] for o in to_cancel] == ["o-up"]
        assert reasons.get("o-up") == "lifecycle_cancel"

"""The shadow fill model: what a resting order would have got, and nothing more.

Conservative by construction. Only volume the tape confirms at the order's own
price can credit a fill; the book-only rule ("level emptied, credit the
remainder") reported a 50% fill rate against a tape-confirmed 3% in the paper
run -- see `core_brain/markets.py:recent_trades`.
"""
from __future__ import annotations

import pytest

from core_brain.shadow_fills import (
    ShadowFill, ShadowRestingOrder, credit_fills, queue_ahead_at,
    queue_multiple,
)


def _order(**kw):
    base = dict(local_id="ord-1", token_id="tok-up", price=0.47,
                size=100.0, filled=0.0, queue_ahead=0.0)
    base.update(kw)
    return ShadowRestingOrder(**base)


def test_queue_ahead_is_the_size_resting_at_our_own_price():
    book = {"bids": {0.48: 500.0, 0.47: 250.0, 0.46: 10.0}, "asks": {}}
    assert queue_ahead_at(book, 0.47) == 250.0


def test_queue_ahead_is_zero_when_no_one_rests_at_our_price():
    book = {"bids": {0.48: 500.0}, "asks": {}}
    assert queue_ahead_at(book, 0.47) == 0.0


def test_traded_volume_fills_the_queue_before_it_fills_us():
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.47: 100.0}})

    assert fills == [ShadowFill("ord-1", "tok-up", 0.47, 40.0)]
    assert queues["ord-1"] == 0.0


def test_volume_smaller_than_the_queue_credits_nothing():
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.47: 25.0}})

    assert fills == []
    assert queues["ord-1"] == 35.0


def test_a_fill_never_exceeds_what_is_left_of_the_order():
    orders = [_order(size=100.0, filled=90.0)]
    fills, _ = credit_fills(orders, {"tok-up": {0.47: 500.0}})

    assert fills == [ShadowFill("ord-1", "tok-up", 0.47, 10.0)]


def test_volume_at_another_token_credits_nothing():
    orders = [_order()]
    fills, _ = credit_fills(orders, {"tok-dn": {0.46: 999.0, 0.47: 999.0}})

    assert fills == []


def test_volume_above_our_price_credits_nothing():
    orders = [_order()]
    fills, _ = credit_fills(orders, {"tok-up": {0.48: 999.0}})

    assert fills == []


def test_sell_print_below_our_bid_clears_queue_and_fills_remainder():
    orders = [_order(queue_ahead=500.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.46: 1.0}})

    assert fills == [ShadowFill("ord-1", "tok-up", 0.47, 100.0)]
    assert queues["ord-1"] == 0.0


def test_trade_through_fills_only_what_is_left():
    orders = [_order(filled=30.0, queue_ahead=500.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.46: 1.0}})

    assert fills == [ShadowFill("ord-1", "tok-up", 0.47, 70.0)]
    assert queues["ord-1"] == 0.0


def test_trade_through_on_a_full_order_credits_no_fill_but_clears_queue():
    orders = [_order(filled=100.0, queue_ahead=50.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.46: 1.0}})

    assert fills == []
    assert queues["ord-1"] == 0.0


def test_one_lower_print_fills_every_order_above_it():
    """The lower bucket is evidence the level cleared, not volume to share."""
    orders = [_order(local_id="ord-1", queue_ahead=200.0),
              _order(local_id="ord-2", queue_ahead=200.0)]
    fills, _ = credit_fills(orders, {"tok-up": {0.46: 1.0}})

    assert fills == [ShadowFill("ord-1", "tok-up", 0.47, 100.0),
                     ShadowFill("ord-2", "tok-up", 0.47, 100.0)]


def test_lower_print_fills_only_orders_above_it():
    orders = [_order(local_id="ord-1", queue_ahead=200.0),
              _order(local_id="ord-2", price=0.45, queue_ahead=10.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.46: 1.0}})

    assert fills == [ShadowFill("ord-1", "tok-up", 0.47, 100.0)]
    assert queues["ord-2"] == 10.0


def test_sweep_evidence_survives_regardless_of_order_sequence():
    """Evidence is snapshotted: an order resting at the print price must not
    consume the proof a higher order needs, whichever comes first."""
    orders = [_order(local_id="ord-lo", price=0.46),
              _order(local_id="ord-hi", price=0.47, queue_ahead=200.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.46: 1.0}})

    assert fills == [ShadowFill("ord-lo", "tok-up", 0.46, 1.0),
                     ShadowFill("ord-hi", "tok-up", 0.47, 100.0)]
    assert queues["ord-hi"] == 0.0


def test_zero_volume_below_our_price_is_not_a_sweep():
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.46: 0.0, 0.47: 100.0}})

    assert fills == [ShadowFill("ord-1", "tok-up", 0.47, 40.0)]
    assert queues["ord-1"] == 0.0


def test_non_finite_volume_below_our_price_is_not_a_sweep():
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.46: float("inf")}})

    assert fills == []
    assert queues["ord-1"] == 60.0


def test_price_that_rounds_to_our_level_uses_the_exact_price_rule():
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.46999: 25.0}})

    assert fills == []
    assert queues["ord-1"] == 35.0


def test_queue_multiple_is_queue_over_size():
    assert queue_multiple(100.0, 5.0) == 20.0
    assert queue_multiple(0.0, 5.0) == 0.0


def test_queue_multiple_returns_none_when_unmeasured():
    assert queue_multiple(None, 5.0) is None
    assert queue_multiple(-1.0, 5.0) is None
    assert queue_multiple(100.0, None) is None
    assert queue_multiple(100.0, 0.0) is None
    assert queue_multiple(100.0, -5.0) is None


def test_queue_multiple_returns_none_for_non_finite_inputs():
    # Review find: NaN/inf would otherwise poison medians and maxima.
    assert queue_multiple(float("nan"), 5.0) is None
    assert queue_multiple(float("inf"), 5.0) is None
    assert queue_multiple(100.0, float("nan")) is None
    assert queue_multiple(100.0, float("inf")) is None


def test_queue_multiple_matches_the_zero_fill_evidence_queues():
    """Issue #351: 2,524 / 5,092 / 38,706 / 6,113 shares ahead at sizes 5-6."""
    multiples = [
        queue_multiple(2524.0, 6.0),
        queue_multiple(5092.0, 6.0),
        queue_multiple(38706.0, 5.0),
        queue_multiple(6113.0, 5.0),
    ]
    assert all(m is not None for m in multiples)
    assert min(multiples) == pytest.approx(420.0, abs=5.0)
    assert max(multiples) == pytest.approx(7741.0, abs=5.0)


def test_queue_multiple_explains_why_deep_queues_never_fill():
    """A multiple of M needs tape volume above the queue ahead, not above M x size."""
    multiple = queue_multiple(60.0, 5.0)
    assert multiple == pytest.approx(12.0)
    shallow, _ = credit_fills([_order(size=5.0, queue_ahead=60.0)],
                              {"tok-up": {0.47: 60.0}})
    assert shallow == []
    deep, _ = credit_fills([_order(size=5.0, queue_ahead=60.0)],
                            {"tok-up": {0.47: 61.0}})
    assert deep == [ShadowFill("ord-1", "tok-up", 0.47, 1.0)]


def test_two_orders_at_one_price_share_the_volume_in_post_order():
    """Earlier order is earlier in the queue. Splitting evenly would credit the
    younger order volume the older one stood in front of."""
    orders = [_order(local_id="old", size=50.0),
              _order(local_id="new", size=50.0)]
    fills, _ = credit_fills(orders, {"tok-up": {0.47: 70.0}})

    assert fills == [ShadowFill("old", "tok-up", 0.47, 50.0),
                     ShadowFill("new", "tok-up", 0.47, 20.0)]

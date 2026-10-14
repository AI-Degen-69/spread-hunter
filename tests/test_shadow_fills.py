"""The shadow fill model: trade-through only, queue position, nothing else.

Conservative by construction. A resting order fills ONLY when the tape prints
strictly below its price -- a trade-through proving the level was cleared. The
old exact-price path (consume the queue ahead, then credit the remainder from
volume at our own price) is removed: queue position cannot be measured from the
public book, so crediting fills from it guessed at a number nobody can verify.
Trade-through fills only on evidence we can see, under-filling rather than
over-filling. `queue_ahead` stays as descriptive telemetry and gates nothing.
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


# --- queue_ahead_at: descriptive depth at our own price (telemetry only) ---

def test_queue_ahead_is_the_size_resting_at_our_own_price():
    book = {"bids": {0.48: 500.0, 0.47: 250.0, 0.46: 10.0}, "asks": {}}
    assert queue_ahead_at(book, 0.47) == 250.0


def test_queue_ahead_is_zero_when_no_one_rests_at_our_price():
    book = {"bids": {0.48: 500.0}, "asks": {}}
    assert queue_ahead_at(book, 0.47) == 0.0


# --- T1 (#531): the clearing print travels on the fill ---


def test_credited_fill_carries_the_closest_print_strictly_below():
    orders = [_order()]
    fills, _ = credit_fills(orders, {"tok-up": {0.46: 1.0, 0.44: 5.0}})

    assert len(fills) == 1
    assert fills[0].clearing_price == 0.46


def test_credited_fill_ignores_prints_at_or_above_our_price():
    orders = [_order()]
    fills, _ = credit_fills(orders, {"tok-up": {0.47: 9.0, 0.46: 1.0}})

    assert fills[0].clearing_price == 0.46


def test_clearing_price_defaults_to_none_and_does_not_affect_equality():
    assert ShadowFill("a", "t", 0.5, 1.0) == ShadowFill(
        "a", "t", 0.5, 1.0, clearing_price=0.48)
    assert ShadowFill("a", "t", 0.5, 1.0).clearing_price is None


# --- trade-through: the only fill path ---

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
    assert "ord-2" not in queues  # 0.46 is not below 0.45, so ord-2 untouched


def test_sweep_evidence_survives_regardless_of_order_sequence():
    """An order resting AT the print price gets nothing (no print strictly
    below it); the higher order still trades through, whichever comes first."""
    orders = [_order(local_id="ord-lo", price=0.46),
              _order(local_id="ord-hi", price=0.47, queue_ahead=200.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.46: 1.0}})

    assert fills == [ShadowFill("ord-hi", "tok-up", 0.47, 100.0)]
    assert "ord-lo" not in queues


# --- no fill without a strictly-lower print ---

def test_exact_price_print_credits_no_fill():
    """A print AT our own price is not a trade-through: no fill, queue
    unchanged. This is the whole point of removing the exact-price path."""
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.47: 100.0}})

    assert fills == []
    assert "ord-1" not in queues


def test_volume_above_our_price_credits_nothing():
    orders = [_order()]
    fills, _ = credit_fills(orders, {"tok-up": {0.48: 999.0}})

    assert fills == []


def test_volume_at_another_token_credits_nothing():
    orders = [_order()]
    fills, _ = credit_fills(orders, {"tok-dn": {0.46: 999.0, 0.47: 999.0}})

    assert fills == []


def test_print_rounding_to_our_level_is_not_a_trade_through():
    """0.46999 rounds to 0.47 == our level, so it is not strictly below."""
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.46999: 25.0}})

    assert fills == []
    assert "ord-1" not in queues


def test_zero_volume_below_our_price_is_not_a_sweep():
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.46: 0.0, 0.47: 100.0}})

    assert fills == []
    assert "ord-1" not in queues


def test_non_finite_volume_below_our_price_is_not_a_sweep():
    orders = [_order(queue_ahead=60.0)]
    fills, queues = credit_fills(orders, {"tok-up": {0.46: float("inf")}})

    assert fills == []
    assert "ord-1" not in queues


# --- queue_multiple: descriptive, unchanged ---

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

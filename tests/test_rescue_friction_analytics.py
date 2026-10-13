"""What a rescue bailout actually cost, separately from planned price risk (#514).

A single-buy leg that never got its partner is unwound through the stop-loss path, and a
taker SELL is the only order this strategy sends that can pay friction: the venue takes no
fee on the maker entries or on a merged pair, so every cent of slippage and fee concentrates
on the losing bailout. `core_brain/single_buy_saver._record_exit_close` already books the
three prices that make the split measurable -- the bid that fired the stop (`trigger_bid`),
the fresh bid the exit actually priced off (`fresh_bid`), and the price the venue gave
(`dn_price`/`up_price`).

The rule these tests hold, the same one `tests/test_statistical_analytics.py` holds:

* a measurement whose inputs were never recorded is `None`, never `0` -- an unmeasured drag
  and a zero drag are different claims, and only one of them belongs in a summary;
* the reference price is a recorded bid or an explicitly tagged assumption, never a number
  invented from something else in the row.

This file starts with the drag decomposition (T1): SLED and its two exact hops.
"""
from __future__ import annotations

import pytest

from core_brain.statistical_analytics import (
    STOP_OFFSET_FALLBACK,
    rescue_drag,
)


def _close(shares: float = 5.0, cost_basis: float = 3.0, proceeds: float = 0.75,
           realized_pnl: float = -2.25, fee: float | None = None,
           trigger_bid: float | None = 0.15, fresh_bid: float | None = 0.15,
           dn_price: float | None = 0.15, up_price: float | None = None,
           method: str = "single_buy_exit") -> dict:
    """One recorded close, in the shape `get_all_closes()` returns."""
    return {"ts": 1_788_000_000.0, "condition_id": "0xm", "market_slug": "m",
            "method": method, "shares": shares, "cost_basis": cost_basis,
            "proceeds": proceeds, "realized_pnl": realized_pnl, "fee": fee,
            "trigger_bid": trigger_bid, "fresh_bid": fresh_bid,
            "dn_price": dn_price, "up_price": up_price}


# --- fixtures ---------------------------------------------------------------
# A: the H2 fresh-book exhibit (`tests/test_hard_stop_fresh_exit.py`): the stop fired at
#    0.15 and the venue gave 0.15. 5 shares bought for $3.00, sold for $0.75.
# B: adverse: the stop fired at 0.20, the book said 0.15 when we acted, we got 0.12.
# C: price improvement: the book drifted toward us between trigger and decision.
# D: the ordinary case for a non-hard-stop rescue -- no `trigger_bid` on the row.
# E: the stale mark this work exists to measure: the stop fired at 0.83, we got 0.15.
A = _close()
B = _close(shares=10.0, cost_basis=6.0, proceeds=1.2, realized_pnl=-4.8, fee=0.30,
           trigger_bid=0.20, fresh_bid=0.15, dn_price=0.12)
C = _close(shares=10.0, cost_basis=6.0, proceeds=2.30, realized_pnl=-3.70, fee=0.0,
           trigger_bid=0.20, fresh_bid=0.22, dn_price=0.23)
D = _close(shares=5.0, cost_basis=3.0, proceeds=2.50, realized_pnl=-0.50, fee=None,
           trigger_bid=None, fresh_bid=0.50, dn_price=0.50)
E = _close(shares=5.0, cost_basis=2.70, proceeds=0.75, realized_pnl=-1.95, fee=0.0,
           trigger_bid=0.83, fresh_bid=0.15, dn_price=0.15)


# --- the drag decomposition -------------------------------------------------

def test_an_exit_that_filled_where_it_triggered_has_no_drag():
    # Arrange -- A: trigger == fresh == sold, and nothing was lost to the fill.
    # Act
    out = rescue_drag(A)

    # Assert
    assert out["exit_price"] == pytest.approx(0.15)
    assert out["reference_bid"] == pytest.approx(0.15)
    assert out["reference_source"] == "recorded"
    assert out["latency_drag_cents"] == pytest.approx(0.0)
    assert out["execution_drag_cents"] == pytest.approx(0.0)
    assert out["sled_cents"] == pytest.approx(0.0)


def test_the_two_hops_are_exactly_the_total():
    # Arrange -- B: 5c of drift between the trigger and the decision, 3c of slippage
    # off the fresh book. SLED is defined as their sum, so the identity is exact on the
    # returned values, not merely close.
    # Act
    out = rescue_drag(B)

    # Assert
    assert out["latency_drag_cents"] == pytest.approx(5.0)
    assert out["execution_drag_cents"] == pytest.approx(3.0)
    assert out["sled_cents"] == out["latency_drag_cents"] + out["execution_drag_cents"]
    assert out["sled_cents"] == pytest.approx(8.0)
    assert out["reference_source"] == "recorded"


def test_the_sum_carries_no_arithmetic_drift():
    # Arrange -- two hops whose sum is not a clean binary fraction. The total is derived
    # by adding the returned parts, not by subtracting from the trigger a second time, so
    # it can never disagree with them by more than the last rounding digit.
    row = _close(shares=10.0, cost_basis=6.0, proceeds=1.0, realized_pnl=-5.0,
                 trigger_bid=0.22, fresh_bid=0.12, dn_price=0.10)

    # Act
    out = rescue_drag(row)

    # Assert
    assert out["latency_drag_cents"] == pytest.approx(10.0)
    assert out["execution_drag_cents"] == pytest.approx(2.0)
    assert out["sled_cents"] == pytest.approx(12.0)
    assert abs(out["sled_cents"]
               - (out["latency_drag_cents"] + out["execution_drag_cents"])) <= 1e-6


def test_an_exit_above_the_trigger_keeps_its_sign():
    # Arrange -- C: the book moved toward us while deciding, which is good news and must
    # not be clipped into a zero. Clipping would erase the one favourable case.
    # Act
    out = rescue_drag(C)

    # Assert
    assert out["latency_drag_cents"] == pytest.approx(-2.0)
    assert out["execution_drag_cents"] == pytest.approx(-1.0)
    assert out["sled_cents"] == pytest.approx(-3.0)


def test_a_missing_trigger_falls_back_to_the_named_offset():
    # Arrange -- D: only the lifecycle hard stop records `trigger_bid`, so a time-based
    # rescue has no observed trigger. The reference is then the named assumption, tagged
    # as such: entry was $3.00 / 5 shares = 0.60, so 0.56.
    # Act
    out = rescue_drag(D)

    # Assert
    assert STOP_OFFSET_FALLBACK == pytest.approx(0.04)
    assert out["reference_bid"] == pytest.approx(0.56)
    assert out["reference_source"] == "assumed_offset"
    # The latency hop needs a trigger bid, and an assumed one would be a fabrication.
    assert out["latency_drag_cents"] is None
    assert out["execution_drag_cents"] == pytest.approx(0.0)
    assert out["sled_cents"] == pytest.approx(6.0)
    # The fresh bid is what we priced off, never the reference we measure against.
    assert out["reference_bid"] != pytest.approx(0.50)


def test_a_stale_mark_shows_up_as_latency_drag():
    # Arrange -- E: the stop's own recorded audit pair. 68c between the bid that fired
    # the stop and the bid the exit priced off, and the venue gave the fresh touch.
    # Act
    out = rescue_drag(E)

    # Assert
    assert out["latency_drag_cents"] == pytest.approx(68.0)
    assert out["execution_drag_cents"] == pytest.approx(0.0)
    assert out["sled_cents"] == pytest.approx(68.0)


# --- which price is the exit ------------------------------------------------

def test_the_recorded_leg_price_is_the_exit():
    # Arrange -- the leg price is the fill the venue reported; `proceeds / shares` is the
    # same number for every row the rescue writer writes. When they disagree, the recorded
    # leg price is the price chain's own field.
    row = _close(dn_price=0.12, proceeds=0.75)

    # Act
    out = rescue_drag(row)

    # Assert
    assert out["exit_price"] == pytest.approx(0.12)


def test_the_up_leg_price_is_read_when_the_down_side_is_absent():
    # Arrange -- a rescue sells ONE leg, and either side can be the one sold.
    row = _close(dn_price=None, up_price=0.13, proceeds=0.75)

    # Act
    out = rescue_drag(row)

    # Assert
    assert out["exit_price"] == pytest.approx(0.13)


def test_the_recorded_proceeds_price_the_exit_when_no_leg_price_was_written():
    # Arrange -- a close with neither leg price but a real sale: $0.75 for 5 shares.
    row = _close(dn_price=None, up_price=None, proceeds=0.75)

    # Act
    out = rescue_drag(row)

    # Assert
    assert out["exit_price"] == pytest.approx(0.15)
    assert out["sled_cents"] == pytest.approx(0.0)


def test_a_close_with_no_price_at_all_measures_nothing():
    # Arrange -- no leg price and no proceeds: there is no exit price to measure against.
    row = _close(dn_price=None, up_price=None, proceeds=None)

    # Act
    out = rescue_drag(row)

    # Assert
    assert out["exit_price"] is None
    assert out["execution_drag_cents"] is None
    assert out["sled_cents"] is None
    assert out["reference_bid"] == pytest.approx(0.15)   # still the recorded trigger
    assert out["reference_source"] == "recorded"


def test_a_close_with_no_shares_and_no_reference_measures_nothing():
    # Arrange -- a row that records neither a share count (so no entry price, no assumed
    # stop) nor a trigger bid. Nothing about this row can be priced.
    row = _close(shares=0.0, trigger_bid=None, fresh_bid=None, proceeds=None)

    # Act
    out = rescue_drag(row)

    # Assert
    assert out["reference_bid"] is None
    assert out["reference_source"] is None
    assert out["latency_drag_cents"] is None
    assert out["sled_cents"] is None


def test_a_close_that_is_not_a_dict_is_read_the_same_way():
    # Arrange -- `kpi.report` hands the module plain dicts, but the registry's frozen
    # record type carries the same fields and must measure identically.
    from core_brain.order_registry import CloseRecord

    record = CloseRecord(ts=1.0, condition_id="0xm", method="single_buy_exit",
                         shares=10.0, dn_price=0.12, cost_basis=6.0, proceeds=1.2,
                         realized_pnl=-4.8, fee=0.30, trigger_bid=0.20, fresh_bid=0.15)

    # Act / Assert
    assert rescue_drag(record) == rescue_drag(B)

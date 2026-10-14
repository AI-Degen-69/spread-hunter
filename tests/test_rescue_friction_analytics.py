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
    FRICTION_BAND_METRICS,
    STOP_OFFSET_FALLBACK,
    friction_band,
    overshoot_ratio,
    rescue_drag,
    rescue_friction_summary,
    taker_cost_drag_ratio,
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
A = _close(fee=0.0)
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


# --- taker cost drag ratio --------------------------------------------------

def test_the_fee_share_of_the_booked_loss_is_measured_against_the_payoff():
    # Arrange -- B: a $0.30 taker fee against a $4.80 booked loss.
    # Act
    out = taker_cost_drag_ratio(B)

    # Assert
    assert out["fee_usd"] == pytest.approx(0.30)
    assert out["fee_measured"] is True
    assert out["realized_pnl_usd"] == pytest.approx(-4.8)
    assert out["tcdr"] == pytest.approx(0.0625)


def test_a_null_fee_is_unmeasured_not_zero():
    # Arrange -- D: the rescue writer leaves `fee` empty. A zero would claim the venue
    # charged nothing; nobody measured whether it did.
    # Act
    out = taker_cost_drag_ratio(D)

    # Assert
    assert out["fee_usd"] is None
    assert out["fee_measured"] is False
    assert out["tcdr"] is None


def test_a_zero_loss_payoff_has_no_fee_share():
    # Arrange -- a close that broke exactly even: the ratio's denominator is zero, and
    # the question has no answer. Returning 0.0 here would read as "fees ate nothing".
    row = _close(fee=0.30, realized_pnl=0.0)

    # Act
    out = taker_cost_drag_ratio(row)

    # Assert
    assert out["tcdr"] is None
    assert out["fee_usd"] == pytest.approx(0.30)
    assert out["fee_measured"] is True


def test_a_close_with_no_payoff_measures_no_fee_share():
    # Arrange / Act
    out = taker_cost_drag_ratio(_close(fee=0.30, realized_pnl=None))

    # Assert
    assert out["realized_pnl_usd"] is None
    assert out["tcdr"] is None


# --- overshoot ratio --------------------------------------------------------

def test_overshoot_is_the_drag_in_units_of_the_risk_the_stop_capped():
    # Arrange -- B: 8c of drag against the 40c the stop intended to risk, so 0.2x.
    # Act
    out = overshoot_ratio(B)

    # Assert
    assert out["planned_risk_cents"] == pytest.approx(40.0)
    assert out["overshoot_ratio"] == pytest.approx(0.2)
    assert out["status"] == "measured"
    assert out["reference_source"] == "recorded"


def test_a_stop_that_held_the_loss_is_not_overshot():
    # Arrange -- A: the exit filled at the trigger, so no loss escaped the stop.
    # Act
    out = overshoot_ratio(A)

    # Assert
    assert out["overshoot_ratio"] == pytest.approx(0.0)
    assert out["status"] == "measured"


def test_price_improvement_reads_as_a_negative_overshoot():
    # Arrange -- C: the book moved our way, so the stop captured less loss than planned.
    # Act
    out = overshoot_ratio(C)

    # Assert
    assert out["overshoot_ratio"] == pytest.approx(-0.075)
    assert friction_band("overshoot", out["overshoot_ratio"]) == "good"


def test_a_drag_past_the_whole_planned_risk_is_a_failed_stop():
    # Arrange -- D: 6c of drag against 4c of planned risk, so the stop let more than it
    # meant to risk escape. This is the reading the metric exists to surface.
    # Act
    out = overshoot_ratio(D)

    # Assert
    assert out["planned_risk_cents"] == pytest.approx(4.0)
    assert out["overshoot_ratio"] == pytest.approx(1.5)
    assert friction_band("overshoot", out["overshoot_ratio"]) == "bad"


def test_a_trigger_at_or_above_entry_has_no_planned_risk_to_overshoot():
    # Arrange -- E: the recorded trigger (0.83) sits above the entry (0.54), so the
    # planned risk is negative and the ratio has no denominator. The guard is what keeps
    # this from being a negative "overshoot", which would read as a stop that did well.
    # Act
    out = overshoot_ratio(E)

    # Assert
    assert out["planned_risk_cents"] == pytest.approx(-29.0)
    assert out["overshoot_ratio"] is None
    assert out["status"] == "no_planned_risk"


def test_a_close_with_no_measurable_drag_has_no_overshoot():
    # Arrange / Act
    out = overshoot_ratio(_close(dn_price=None, up_price=None, proceeds=None))

    # Assert
    assert out["overshoot_ratio"] is None
    assert out["status"] == "unmeasured"


# --- traffic-light bands ----------------------------------------------------

@pytest.mark.parametrize("metric,value,expected", [
    ("sled", None, "unmeasured"),
    # A non-finite value is not a bad value, it is no value: `build()` promises strict JSON,
    # so this reaches the band only from a caller holding its own arithmetic.
    ("sled", float("nan"), "unmeasured"),
    ("sled", 0.0, "good"),
    ("sled", -3.0, "good"),
    ("sled", 2.0, "average"),
    ("sled", 2.01, "bad"),
    ("latency", 0.0, "good"),
    ("latency", -1.0, "good"),
    ("latency", 2.0, "average"),
    ("latency", 2.01, "bad"),
    ("latency", 34.0, "bad"),
    ("execution", 0.0, "good"),
    ("execution", 2.0, "good"),
    ("execution", 2.01, "average"),
    ("execution", 5.0, "average"),
    ("execution", 5.01, "bad"),
    ("tcdr", 0.0, "good"),
    ("tcdr", 0.0625, "average"),
    ("tcdr", 0.0999, "average"),
    ("tcdr", 0.10, "bad"),
    ("overshoot", 0.0, "good"),
    ("overshoot", -0.075, "good"),
    ("overshoot", 1.0, "average"),
    ("overshoot", 1.5, "bad"),
])
def test_each_metric_is_banded_on_the_tickets_own_edges(metric, value, expected):
    # Act / Assert
    assert friction_band(metric, value) == expected


def test_a_band_name_that_does_not_exist_is_a_bug_not_a_measurement():
    # Arrange -- an unknown name returning "unmeasured" would read as missing data and
    # hide the typo behind a plausible-looking reading.
    # Act / Assert
    with pytest.raises(ValueError):
        friction_band("sledd", 1.0)


def test_the_banded_metric_names_are_the_five_the_ticket_names():
    # Act / Assert
    assert FRICTION_BAND_METRICS == ("sled", "latency", "execution", "tcdr", "overshoot")


# --- the summary -----------------------------------------------------------

MIXED = [A, B, C, D, E,
         _close(method="shadow_merge"),
         _close(method="venue_sync"),
         _close(method="sell")]


def test_the_summary_averages_each_metric_over_its_own_measured_subset():
    # Arrange -- five rescue closes. The latency hop is measurable on four of them (D has
    # no recorded trigger), and the fee ratio on four (D's fee is NULL). Each mean uses
    # the closes that carry it, so one unmeasured row cannot dilute a measurement.
    # Act
    out = rescue_friction_summary([A, B, C, D, E])

    # Assert
    assert out["rescue_count"] == 5
    assert out["measured_count"] == 5
    assert out["mean_sled_cents"] == pytest.approx(15.8)
    assert out["mean_latency_drag_cents"] == pytest.approx(71.0 / 4.0)   # 0, 5, -2, 68
    assert out["mean_execution_drag_cents"] == pytest.approx(0.4)
    assert out["mean_tcdr"] == pytest.approx(0.015625)
    assert out["mean_overshoot_ratio"] == pytest.approx(0.40625)
    assert out["units"] == "cents_per_share"


def test_the_summary_discloses_how_the_reference_was_arrived_at():
    # Arrange -- four of the five rescue closes recorded a trigger bid; one fell back to
    # the named offset. The count is the reader's only way to tell which figures rest on
    # an assumption.
    # Act
    out = rescue_friction_summary([A, B, C, D, E])

    # Assert
    assert out["recorded_reference_count"] == 4
    assert out["assumed_reference_count"] == 1
    assert out["fee_measured_count"] == 4
    # The offset the disclosure names travels in the payload, so the card prints the
    # number the computation actually used instead of a copy of its own.
    assert out["assumed_stop_offset"] == pytest.approx(STOP_OFFSET_FALLBACK)


def test_the_summary_bands_the_means_it_reports():
    # Arrange -- the band is a function of the value, never a static label.
    # Act
    out = rescue_friction_summary([A, B, C, D, E])

    # Assert
    assert set(out["bands"]) == set(FRICTION_BAND_METRICS)
    assert out["bands"]["sled"] == "bad"                # 15.8c mean, worst close 68c
    assert out["bands"]["latency"] == "bad"            # 17.75c mean adverse drift
    assert out["bands"]["execution"] == "good"         # 0.4c mean
    assert out["bands"]["tcdr"] == "average"           # 1.6% of the booked loss
    assert out["bands"]["overshoot"] == "average"      # 0.41x the planned risk


def test_the_summary_leaves_non_rescue_closes_out_of_every_measurement():
    # Arrange -- a merge and a bookkeeping row are not bailouts, and a generic sell is
    # neither path. They are counted and disclosed, never averaged in.
    # Act
    out = rescue_friction_summary(MIXED)

    # Assert
    assert out["rescue_count"] == 5
    assert out["non_rescue_count"] == 3
    assert out["mean_sled_cents"] == pytest.approx(15.8)


def test_a_method_recorded_with_stray_case_still_counts_as_a_rescue():
    # Arrange -- `method` is a free-form recorded string.
    # Act
    out = rescue_friction_summary([_close(method=" Single_Buy_Exit ")])

    # Assert
    assert out["rescue_count"] == 1
    assert out["measured_count"] == 1


def test_a_run_with_no_rescue_closes_reports_unmeasured_not_zero():
    # Arrange / Act -- the section is always present, like `fill_path_edge`, so a caller
    # never branches on whether the key exists. `0.0` here would claim bailouts cost
    # nothing.
    out = rescue_friction_summary([_close(method="shadow_merge")])

    # Assert
    assert out["rescue_count"] == 0
    assert out["measured_count"] == 0
    assert out["mean_sled_cents"] is None
    assert out["mean_tcdr"] is None
    assert out["mean_overshoot_ratio"] is None
    assert out["bands"]["sled"] == "unmeasured"
    assert all(band == "unmeasured" for band in out["bands"].values())


def test_the_summary_of_nothing_is_the_same_shape():
    # Arrange / Act
    out = rescue_friction_summary([])

    # Assert
    assert out["rescue_count"] == 0
    assert out["non_rescue_count"] == 0
    assert out["recorded_reference_count"] == 0
    assert out["assumed_reference_count"] == 0
    assert out["fee_measured_count"] == 0
    assert out["mean_latency_drag_cents"] is None
    assert out["mean_execution_drag_cents"] is None


def test_the_summary_is_strict_json_on_every_shape():
    # Arrange / Act / Assert -- `inf` or `nan` in the payload would break the strict-JSON
    # promise `build()` makes to its callers.
    import json

    for rows in ([], MIXED, [_close(realized_pnl=0.0, shares=0.0, cost_basis=None)]):
        json.dumps(rescue_friction_summary(rows), allow_nan=False)


def test_the_summary_never_mutates_the_closes_it_reads():
    # Arrange
    rows = [dict(row) for row in MIXED]
    before = [dict(row) for row in rows]

    # Act
    rescue_friction_summary(rows)

    # Assert
    assert rows == before


def test_a_summary_of_records_measures_like_a_summary_of_dicts():
    # Arrange -- the registry's frozen record type carries the same prices.
    from core_brain.order_registry import CloseRecord

    record = CloseRecord(ts=1.0, condition_id="0xm", method="single_buy_exit",
                         shares=10.0, dn_price=0.12, cost_basis=6.0, proceeds=1.2,
                         realized_pnl=-4.8, fee=0.30, trigger_bid=0.20, fresh_bid=0.15)

    # Act / Assert
    assert rescue_friction_summary([record]) == rescue_friction_summary([B])


def test_a_close_that_is_not_a_dict_is_read_the_same_way():
    # Arrange -- `kpi.report` hands the module plain dicts, but the registry's frozen
    # record type carries the same fields and must measure identically.
    from core_brain.order_registry import CloseRecord

    record = CloseRecord(ts=1.0, condition_id="0xm", method="single_buy_exit",
                         shares=10.0, dn_price=0.12, cost_basis=6.0, proceeds=1.2,
                         realized_pnl=-4.8, fee=0.30, trigger_bid=0.20, fresh_bid=0.15)

    # Act / Assert
    assert rescue_drag(record) == rescue_drag(B)

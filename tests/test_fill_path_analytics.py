"""Merge edge versus rescue bleed, measured apart (#513).

Aggregate PnL hides which resolution path drives it. A merged pair captures the
spread with no fee and no slippage; a single-buy rescue is a forced taker exit
somebody paid for. A healthy merge edge can sit on top of a rescue bleed, and a
single number cannot say which one is happening.

The rule these tests hold is the same one `tests/test_statistical_analytics.py`
holds for the charts: **a value that cannot be measured is `None`, never a
fabricated zero.** A rescue path with no closes has no rescue mean and no
break-even rate -- reporting `0.00` there would read as "rescues are free".

The buckets are an accounting proxy, not literal `PAIRED_MERGE` / `RESCUE_STOP`
execution labels. `method` is a free-form recorded string, so the classifier is
the load-bearing mapping and the tests pin it against the repo's own constants.
"""
from __future__ import annotations

import json
import math

import pytest

from core_brain.order_registry import CloseRecord
from core_brain.statistical_analytics import (
    EXCLUDED_METHODS,
    MERGE_METHODS,
    RESCUE_METHODS,
    break_even_rescue_rate,
    fill_path_edge,
)


def _close(pnl, method: str = "shadow_merge", condition_id: str = "0xm") -> dict:
    return {"ts": 1_788_000_000.0, "condition_id": condition_id, "market_slug": "m",
            "method": method, "shares": 6.0, "cost_basis": 5.70,
            "proceeds": 5.70 + pnl, "realized_pnl": pnl}


# --- The two paths, on runs that contain only one of them -------------------

def test_an_all_merge_run_measures_only_the_merge_path():
    # Arrange -- a rehearsal's two merged pairs.
    closes = [_close(0.30, "merge"), _close(0.10, "shadow_merge")]

    # Act
    out = fill_path_edge(closes)

    # Assert
    assert out["merge_count"] == 2
    assert out["merge_pnl_usd"] == pytest.approx(0.40)
    assert out["merge_expectancy_usd"] == pytest.approx(0.20)
    assert out["rescue_count"] == 0
    assert out["rescue_pnl_usd"] == pytest.approx(0.0)
    assert out["rescue_expectancy_usd"] is None
    # No rescue closes at all: the rate is a real, measured 0.0 -- there were
    # resolved closes and none of them was a rescue.
    assert out["rescue_rate"] == pytest.approx(0.0)
    assert out["net_expectancy_usd"] == pytest.approx(0.20)
    assert out["break_even_rescue_rate"] is None
    assert out["break_even_status"] == "unmeasured"
    assert out["units"] == "usd_per_close"


def test_an_all_rescue_run_measures_only_the_rescue_path():
    # Arrange -- a run where nothing merged and both legs were dumped.
    closes = [_close(-0.40, "single_buy_exit"), _close(-0.20, "ladder_exit")]

    # Act
    out = fill_path_edge(closes)

    # Assert
    assert out["merge_count"] == 0
    assert out["merge_expectancy_usd"] is None
    assert out["rescue_count"] == 2
    assert out["rescue_expectancy_usd"] == pytest.approx(-0.30)
    assert out["rescue_rate"] == pytest.approx(1.0)
    assert out["net_expectancy_usd"] == pytest.approx(-0.30)
    # Nothing merged, so there is no merge mean to balance a rescue against.
    assert out["break_even_rescue_rate"] is None
    assert out["break_even_status"] == "unmeasured"


# --- The mixed run, and what stays outside both paths -----------------------

def test_a_mixed_run_splits_both_paths_and_discloses_the_rest():
    # Arrange -- three merges, one naked exit, one settlement, one sync row.
    closes = [_close(0.30, "merge"), _close(0.30, "merge"), _close(0.30, "merge"),
              _close(-0.60, "naked_exit"),
              _close(1.00, "shadow_settlement"),
              _close(5.00, "venue_sync")]

    # Act
    out = fill_path_edge(closes)

    # Assert -- the two paths.
    assert out["merge_count"] == 3
    assert out["merge_expectancy_usd"] == pytest.approx(0.30)
    assert out["rescue_count"] == 1
    assert out["rescue_expectancy_usd"] == pytest.approx(-0.60)
    assert out["rescue_rate"] == pytest.approx(0.25)
    assert out["net_expectancy_usd"] == pytest.approx(0.075)
    assert out["break_even_rescue_rate"] == pytest.approx(0.333333, abs=1e-6)
    assert out["break_even_status"] == "measured"

    # The disclosure that stops a reader mistaking this for the run's PnL.
    assert out["other_count"] == 1
    assert out["other_pnl_usd"] == pytest.approx(1.0)
    assert out["excluded_count"] == 1
    assert out["skipped_count"] == 0

    # Net expectancy is the weighted average of the two paths, and the
    # settlement's +$1.00 and the sync row's +$5.00 moved nothing.
    assert out["net_expectancy_usd"] == pytest.approx(
        0.75 * out["merge_expectancy_usd"] + 0.25 * out["rescue_expectancy_usd"])
    assert out["net_expectancy_usd"] != pytest.approx(
        (0.90 - 0.60 + 1.00 + 5.00) / 6)


def test_a_settled_close_is_disclosed_and_never_becomes_a_rescue():
    # Arrange -- a run whose only close is a leg held to market resolution.
    # Held to settlement is not a taker stop-loss exit, which is what the
    # ticket means by a rescue, so it is counted and shown but not bucketed.
    out = fill_path_edge([_close(1.00, "shadow_settlement")])

    # Assert
    assert out["other_count"] == 1
    assert out["other_pnl_usd"] == pytest.approx(1.0)
    assert out["merge_count"] == 0
    assert out["rescue_count"] == 0


# --- Nothing to measure -----------------------------------------------------

def test_an_empty_run_reports_nothing_rather_than_zero():
    # Arrange -- a dashboard opened before any close exists.
    out = fill_path_edge([])

    # Assert
    for count in ("merge_count", "rescue_count", "other_count",
                  "excluded_count", "skipped_count"):
        assert out[count] == 0
    for missing in ("merge_expectancy_usd", "rescue_expectancy_usd", "rescue_rate",
                    "net_expectancy_usd", "break_even_rescue_rate"):
        assert out[missing] is None
    assert out["break_even_status"] == "unmeasured"


def test_a_run_with_no_resolved_path_has_no_rate():
    # Arrange -- every close is outside both paths: the divisor is zero, so a
    # rate would be a division by nothing rather than a measurement.
    out = fill_path_edge([_close(1.00, "shadow_settlement"),
                          _close(0.50, "venue_sync")])

    # Assert
    assert out["rescue_rate"] is None
    assert out["net_expectancy_usd"] is None
    assert out["other_count"] == 1
    assert out["excluded_count"] == 1


# --- The break-even threshold ----------------------------------------------

def test_the_break_even_rate_is_the_rescue_share_that_cancels_the_merge_edge():
    # Arrange -- a 30c merge edge against a 60c rescue loss. Losing 60c once
    # cancels two 30c wins, so the run breaks even at one rescue in three.
    value, status = break_even_rescue_rate(0.30, -0.60)

    # Assert
    assert status == "measured"
    assert value == pytest.approx(1.0 / 3.0)
    # Sanity: the weighted net is exactly zero at that rate, positive below it.
    assert 0.30 * (1 - value) + (-0.60) * value == pytest.approx(0.0)


@pytest.mark.parametrize("merge_mean, rescue_mean, status", [
    (0.0, 0.0, "merge_not_profitable"),
    (-0.10, -0.50, "merge_not_profitable"),
    (0.20, 0.0, "rescue_not_losing"),
    (0.20, 0.15, "rescue_not_losing"),
    (0.20, None, "unmeasured"),
    (None, -0.50, "unmeasured"),
    (None, None, "unmeasured"),
])
def test_the_break_even_refuses_to_answer_when_it_cannot(merge_mean, rescue_mean, status):
    # Arrange / Act -- a case with no break-even rate gets no number and a
    # reason, in that order of precedence.
    value, got_status = break_even_rescue_rate(merge_mean, rescue_mean)

    # Assert
    assert value is None
    assert got_status == status


def test_the_payload_reports_its_own_break_even_status():
    # Arrange -- a losing rescue against a profitable merge is measurable.
    out = fill_path_edge([_close(0.30, "merge"), _close(-0.30, "single_buy_exit")])

    # Assert -- equal and opposite, so the run breaks even at a 50% rescue rate.
    assert out["break_even_status"] == "measured"
    assert out["break_even_rescue_rate"] == pytest.approx(0.5)


# --- The classifier ---------------------------------------------------------

def test_a_padded_or_upper_case_method_still_classifies():
    # Arrange -- the registry writes literal strings, so padding only ever
    # arrives from a hand-written row; it must not fall into `other` over it.
    out = fill_path_edge([_close(0.30, " Shadow_Merge ")])

    # Assert
    assert out["merge_count"] == 1
    assert out["other_count"] == 0


def test_a_method_nobody_classified_is_other_not_dropped():
    # Arrange -- `sell` is the dataclass default and no writer passes it.
    out = fill_path_edge([_close(0.10, "sell"), _close(0.10, "")])

    # Assert -- both surface rather than vanishing, so a new method string is
    # visible in the disclosure instead of silently uncounted.
    assert out["other_count"] == 2
    assert out["other_pnl_usd"] == pytest.approx(0.20)
    assert out["merge_count"] == 0
    assert out["rescue_count"] == 0


# --- Bad numbers, partial closes, input shapes -----------------------------

def test_non_finite_pnl_is_skipped_and_moves_nothing():
    # Arrange -- NaN, infinity, a missing field and a non-numeric string.
    closes = [_close(0.30, "merge"),
              _close(math.nan, "merge"),
              _close(float("inf"), "merge"),
              _close(-0.40, "single_buy_exit"),
              {"ts": 1.0, "condition_id": "0xy", "method": "merge"},
              {"ts": 1.0, "condition_id": "0xz", "method": "merge",
               "realized_pnl": "abc"}]

    # Act
    out = fill_path_edge(closes)

    # Assert -- four unusable rows, and the one good merge still sets the mean.
    assert out["skipped_count"] == 4
    assert out["merge_count"] == 1
    assert out["merge_expectancy_usd"] == pytest.approx(0.30)
    assert out["rescue_count"] == 1
    assert out["rescue_expectancy_usd"] == pytest.approx(-0.40)
    assert out["net_expectancy_usd"] == pytest.approx((0.30 - 0.40) / 2)


def test_two_partial_closes_of_one_market_are_two_observations():
    # Arrange -- two sells against the same market.
    closes = [_close(-0.40, "single_buy_exit", "0xdup"),
              _close(-0.20, "single_buy_exit", "0xdup")]

    # Assert
    assert fill_path_edge(closes)["rescue_count"] == 2


def test_close_records_and_plain_dicts_give_the_same_answer():
    # Arrange -- `kpi.report` passes the registry's dicts; a caller may hold
    # records. Both shapes must measure identically.
    records = [
        CloseRecord(ts=1_788_000_000.0, condition_id="0xm", method="shadow_merge",
                    shares=6.0, cost_basis=5.70, realized_pnl=0.30),
        CloseRecord(ts=1_788_000_000.0, condition_id="0xm", method="single_buy_exit",
                    shares=6.0, cost_basis=5.70, realized_pnl=-0.60),
    ]

    # Act / Assert
    assert fill_path_edge(records) == fill_path_edge(
        [_close(0.30, "shadow_merge"), _close(-0.60, "single_buy_exit")])


def test_the_call_is_pure():
    # Arrange
    closes = [_close(0.30, "merge"), _close(-0.60, "single_buy_exit")]
    before = json.loads(json.dumps(closes))

    # Act
    first, second = fill_path_edge(closes), fill_path_edge(closes)

    # Assert -- same answer twice, and the caller's list is untouched.
    assert first == second
    assert closes == before


@pytest.mark.parametrize("closes", [
    [],
    [_close(0.30, "merge"), _close(-0.60, "single_buy_exit"),
     _close(1.00, "shadow_settlement")],
    [_close(math.nan, "merge"), _close(float("inf"), "venue_sync")],
])
def test_the_payload_is_safe_to_serialise_as_strict_json(closes):
    # Arrange / Act / Assert -- a NaN leaking into the payload would make the
    # dashboard's JSON parse fail, so strict mode is the contract.
    json.dumps(fill_path_edge(closes), allow_nan=False)


def test_finite_rows_that_overflow_their_aggregate_are_skipped_not_emitted():
    # Arrange -- each record is finite on its own, but the running total is not:
    # two closes of 1e308 sum to `inf`. An overflowed aggregate is not a
    # measurement, so the row that would produce it is skipped like a NaN one.
    # The per-row guard alone cannot catch this -- the overflow happens in the
    # sum -- so the payload would otherwise carry `Infinity` and fail strict
    # JSON, which is the very contract the guard exists to keep.
    closes = [_close(1e308, "merge"), _close(1e308, "merge")]

    # Act
    out = fill_path_edge(closes)

    # Assert
    assert out["merge_count"] == 1
    assert out["skipped_count"] == 1
    assert math.isfinite(out["merge_pnl_usd"])
    assert math.isfinite(out["merge_expectancy_usd"])
    assert math.isfinite(out["net_expectancy_usd"])
    json.dumps(out, allow_nan=False)


# --- The classifier is pinned to the repo's own constants -------------------

def test_the_method_sets_match_their_canonical_homes():
    # Arrange -- this module re-declares the sets rather than importing them,
    # because `kpi` imports `statistical_analytics` and the reverse is a cycle.
    # The duplication is therefore pinned here instead of trusted.
    from core_brain import kpi, ladder, registry_state
    from statistical_validation_run import artifacts

    # Act / Assert
    assert set(MERGE_METHODS) == set(registry_state.MERGE_CLOSE_METHODS)
    for home in (artifacts.EXIT_METHODS, kpi.SINGLE_BUY_EXIT_METHODS,
                 ladder.LADDER_EXIT_METHODS):
        assert set(RESCUE_METHODS) == set(home)
    assert set(EXCLUDED_METHODS) == set(kpi.NON_TRADE_CLOSE_METHODS)

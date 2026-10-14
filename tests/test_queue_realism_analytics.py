"""What a trade-through fill says about where the tape was, in cents (#515).

The shadow fill model credits a resting order ONLY when the tape prints strictly below its
price -- a trade-through proving the level cleared. Three of the metrics once planned around
this fill model measure either a tautology or an unverifiable guess under that rule, so the
ticket removed them rather than deferring them: a "touch" without a clearing print is the sim
working correctly, a drain ratio is satisfied by construction on every credited fill, and a
depth correlation inherits the unmeasurable `queue_ahead` guess on one of its two axes. What
survives is measured from prints the sim can actually see.

This file holds the survivors, and it follows the rule `tests/test_statistical_analytics.py`
holds: a measurement whose inputs were never recorded is `None`, never `0`. An unmeasured
depth and a depth of zero are different claims.

The depth section lands first (T1). Percentiles are linearly interpolated, so the median here
is the median `statistics.median` and `kpi.median_*` mean -- pinned below, because the
traffic-light band rides on that number.
"""
from __future__ import annotations

import json
import statistics

import pytest

from core_brain.statistical_analytics import (
    ClearedFill,
    cleared_fills,
    fill_latency,
    trade_through_depth,
    trade_through_depth_band,
)


def _fill(order_id: str = "o", order_price: float | None = 0.50,
          clearing_price: float | None = 0.48, placed_ts: float | None = 1000.0,
          filled_ts: float | None = 1005.0) -> ClearedFill:
    """One credited fill and the print that cleared its level."""
    return ClearedFill(order_id=order_id, order_price=order_price,
                       clearing_price=clearing_price, placed_ts=placed_ts,
                       filled_ts=filled_ts)


def _depth(depth_cents: float, order_id: str = "o") -> ClearedFill:
    """A fill whose clearing print sat `depth_cents` below our own price."""
    return _fill(order_id=order_id, order_price=0.50,
                 clearing_price=round(0.50 - depth_cents / 100.0, 6))


# --- T1: trade-through depth, in cents --------------------------------------


def test_depth_is_the_gap_between_our_price_and_the_clearing_print():
    assert trade_through_depth([_fill(clearing_price=0.48)])["median_cents"] == 2.0
    assert trade_through_depth([_fill(clearing_price=0.495)])["median_cents"] == 0.5


def test_percentiles_interpolate_so_the_median_is_the_median():
    """`p50` must be the number `statistics.median` returns, not nearest-rank.

    On an even sample nearest-rank picks the lower middle value (2.0 here), which is a
    different number from the median every other reader in this repo reports, and the
    band is computed from this figure.
    """
    depths = [0.5, 2.0, 4.0, 5.0]
    result = trade_through_depth([_depth(d) for d in depths])

    assert result["count"] == 4
    assert result["median_cents"] == 3.0
    assert result["median_cents"] == statistics.median(depths)
    # index 0.75 * 3 = 2.25 -> 4.0 + 0.25 * (5.0 - 4.0)
    assert result["p75_cents"] == 4.25
    assert result["band"] == "average"


def test_single_fill_reports_its_own_depth_for_both_percentiles():
    result = trade_through_depth([_depth(2.5)])

    assert result["count"] == 1
    assert result["median_cents"] == 2.5
    assert result["p75_cents"] == 2.5


@pytest.mark.parametrize("median_cents,expected", [
    (0.0, "good"),
    (0.99, "good"),
    (1.0, "average"),
    (3.0, "average"),
    (3.01, "bad"),
    (15.0, "bad"),
    (None, "unmeasured"),
    (float("nan"), "unmeasured"),
    (float("inf"), "unmeasured"),
])
def test_band_edges_match_the_settled_table(median_cents, expected):
    assert trade_through_depth_band(median_cents) == expected


def test_negative_depth_band_is_unmeasured_not_good():
    """The table starts at 0. A negative depth is a print that did not trade through, so
    the honest label is "no such reading" -- not the good side of a table it is not on."""
    assert trade_through_depth_band(-2.0) == "unmeasured"


def test_empty_depth_run_is_none_not_zero_and_never_divides_by_zero():
    result = trade_through_depth([])

    assert result == {"count": 0, "missing": 0, "not_trade_through": 0,
                      "median_cents": None, "p75_cents": None,
                      "band": "unmeasured"}


def test_missing_prices_are_counted_and_the_rest_still_measures():
    rows = [
        _fill("a", clearing_price=0.48),
        _fill("b", order_price=None),
        _fill("c", clearing_price=None),
        _fill("d", clearing_price=float("nan")),
        _fill("e", order_price=float("inf")),
        _fill("f", clearing_price=0.46),
    ]
    result = trade_through_depth(rows)

    assert result["count"] == 2
    assert result["missing"] == 4
    assert result["not_trade_through"] == 0
    assert result["median_cents"] == 3.0
    assert result["band"] == "average"


def test_a_row_that_is_not_a_trade_through_is_disclosed_not_measured():
    """`equal` is the exact-price path the fill fix removed; `above` is a buy above our
    price. Neither can produce a trade-through depth, and neither may be folded into the
    percentiles as a zero."""
    rows = [
        _fill("a", clearing_price=0.48),
        _fill("b", clearing_price=0.50),
        _fill("c", clearing_price=0.52),
    ]
    result = trade_through_depth(rows)

    assert result["count"] == 1
    assert result["missing"] == 0
    assert result["not_trade_through"] == 2
    assert result["median_cents"] == 2.0


def test_the_three_counts_partition_the_input():
    rows = [_fill("a", clearing_price=0.48),
            _fill("b", order_price=None),
            _fill("c", clearing_price=0.52)]
    result = trade_through_depth(rows)

    assert (result["count"] + result["missing"] + result["not_trade_through"]
            == len(rows))


def test_input_order_does_not_change_the_answer():
    rows = [_depth(0.5, "a"), _depth(2.0, "b"), _depth(4.0, "c"), _depth(5.0, "d")]

    assert trade_through_depth(list(reversed(rows))) == trade_through_depth(rows)


def test_input_is_not_mutated():
    rows = [_depth(2.0, "a"), _depth(4.0, "b")]
    before = [(r.order_id, r.order_price, r.clearing_price) for r in rows]

    trade_through_depth(rows)

    assert [(r.order_id, r.order_price, r.clearing_price) for r in rows] == before


def test_result_is_strict_json():
    rows = [_depth(2.0, "a"), _depth(4.0, "b")]

    payload = json.dumps(trade_through_depth(rows), allow_nan=False)

    assert "NaN" not in payload and "Infinity" not in payload


# --- T2: fill latency, in seconds -------------------------------------------


def test_wait_is_the_gap_between_placement_and_the_fill():
    result = fill_latency([_fill(placed_ts=1000.0, filled_ts=1005.0)])

    assert result["median_sec"] == 5.0


def test_median_and_p90_wait_interpolate():
    rows = [_fill("a", placed_ts=1000.0, filled_ts=1005.0),
            _fill("b", placed_ts=1000.0, filled_ts=1010.0),
            _fill("c", placed_ts=1000.0, filled_ts=1015.0)]
    result = fill_latency(rows)

    assert result["count"] == 3
    assert result["median_sec"] == 10.0
    # index 0.90 * 2 = 1.8 -> 10.0 + 0.8 * (15.0 - 10.0)
    assert result["p90_sec"] == 14.0


def test_single_wait_reports_for_both_percentiles():
    result = fill_latency([_fill(placed_ts=1000.0, filled_ts=1007.5)])

    assert result["count"] == 1
    assert result["median_sec"] == 7.5
    assert result["p90_sec"] == 7.5


def test_a_same_second_fill_is_a_measured_zero_wait():
    """A wait of zero is a measurement; only an unrecorded wait is `None`."""
    result = fill_latency([_fill(placed_ts=1000.0, filled_ts=1000.0)])

    assert result["count"] == 1
    assert result["excluded"] == 0
    assert result["median_sec"] == 0.0


def test_clock_skew_is_excluded_not_clamped_to_zero():
    """`kpi.median_seconds_to_fill` clamps a negative wait to 0.0. That reports a fill
    that never rested as an instant fill, which is a claim nobody measured, so here the
    row is excluded and counted instead."""
    result = fill_latency([_fill("a", placed_ts=1000.0, filled_ts=990.0),
                           _fill("b", placed_ts=1000.0, filled_ts=1005.0)])

    assert result["count"] == 1
    assert result["excluded"] == 1
    assert result["median_sec"] == 5.0


def test_missing_timestamps_are_excluded_not_zero():
    rows = [_fill("a", placed_ts=None),
            _fill("b", filled_ts=None),
            _fill("c", placed_ts=float("nan")),
            _fill("d", filled_ts=float("inf")),
            _fill("e", placed_ts=None, filled_ts=None)]
    result = fill_latency(rows)

    assert result["count"] == 0
    assert result["excluded"] == 5
    assert result["median_sec"] is None
    assert result["p90_sec"] is None


def test_empty_latency_run_is_none_not_zero_and_never_divides_by_zero():
    assert fill_latency([]) == {"count": 0, "excluded": 0,
                                "median_sec": None, "p90_sec": None}


def test_fill_latency_has_no_band():
    """A wait has no market reference to be judged against, so no good/bad label may
    exist for it -- the same rule that leaves queue depth unbanded."""
    result = fill_latency([_fill()])

    assert "band" not in result
    assert "bands" not in result


def test_the_two_metrics_are_independent():
    """Prices without timestamps still measure depth; timestamps without prices still
    measure a wait. Neither field set may silently zero the other metric."""
    prices_only = _fill("a", order_price=0.50, clearing_price=0.46,
                        placed_ts=None, filled_ts=None)
    times_only = _fill("b", order_price=None, clearing_price=None,
                       placed_ts=1000.0, filled_ts=1006.0)

    depth = trade_through_depth([prices_only, times_only])
    wait = fill_latency([prices_only, times_only])

    assert depth["count"] == 1
    assert depth["median_cents"] == 4.0
    assert depth["missing"] == 1
    assert wait["count"] == 1
    assert wait["median_sec"] == 6.0
    assert wait["excluded"] == 1


def test_latency_counts_partition_the_input():
    rows = [_fill("a", placed_ts=1000.0, filled_ts=1005.0),
            _fill("b", placed_ts=None),
            _fill("c", placed_ts=1000.0, filled_ts=990.0)]
    result = fill_latency(rows)

    assert result["count"] + result["excluded"] == len(rows)


def test_latency_ignores_input_order():
    rows = [_fill("a", placed_ts=1000.0, filled_ts=1005.0),
            _fill("b", placed_ts=1000.0, filled_ts=1010.0),
            _fill("c", placed_ts=1000.0, filled_ts=1015.0)]

    assert fill_latency(list(reversed(rows))) == fill_latency(rows)


def test_latency_does_not_mutate_input():
    rows = [_fill("a", placed_ts=1000.0, filled_ts=1005.0)]

    fill_latency(rows)

    assert rows[0].placed_ts == 1000.0 and rows[0].filled_ts == 1005.0


def test_latency_result_is_strict_json():
    payload = json.dumps(fill_latency([_fill()]), allow_nan=False)

    assert "NaN" not in payload and "Infinity" not in payload


# --- T3: records built from the rows the registry already holds -------------


def _quote(local_id: str = "q1", ts: float | None = 1000.0,
           price: float | None = 0.50) -> dict:
    """One `quotes` row, in the shape `get_all_quotes()` returns."""
    return {"id": 1, "ts": ts, "local_id": local_id, "price": price,
            "side": "BUY", "filled": 0.0, "fill_ts": None, "cancelled": 0}


def _fill_row(order_uuid: str = "q1", venue_ts: int | None = 1_005_000,
              recorded_ts: int | None = 1_005_000, size: float = 5.0,
              clearing_price: float | None = None) -> dict:
    """One `fills` row, in the shape `get_all_fills()` returns."""
    return {"trade_id": "t", "order_uuid": order_uuid, "size": size,
            "price": 0.50, "venue_ts": venue_ts, "recorded_ts": recorded_ts,
            "clearing_price": clearing_price}


def test_cleared_fills_joins_a_quote_to_its_earliest_fill_in_seconds():
    records = cleared_fills([_quote("q1", ts=1000.0)],
                            [_fill_row("q1", venue_ts=1_015_000),
                             _fill_row("q1", venue_ts=1_005_000, size=2.0)])

    assert len(records) == 1
    assert records[0].order_id == "q1"
    assert records[0].order_price == 0.50
    assert records[0].placed_ts == 1000.0
    # Earliest fill, milliseconds -> seconds: the wait is measured from placement to the
    # first credit, and averaging the last partial in would shorten every reading.
    assert records[0].filled_ts == 1005.0
    # Rows from stores that never recorded a print keep None — a measurement
    # nobody took, not a depth of zero.
    assert records[0].clearing_price is None


def test_cleared_fills_reads_the_print_from_the_earliest_fill_row():
    """Latency and depth describe the same fill event (#531)."""
    records = cleared_fills(
        [_quote("q1", ts=1000.0)],
        [_fill_row("q1", venue_ts=1_015_000, clearing_price=0.47),
         _fill_row("q1", venue_ts=1_005_000, size=2.0,
                    clearing_price=0.48)])

    assert records[0].filled_ts == 1005.0
    assert records[0].clearing_price == 0.48
    assert trade_through_depth(records)["median_cents"] == 2.0


def test_cleared_fills_keeps_a_missing_print_missing():
    records = cleared_fills([_quote("q1", ts=1000.0)],
                            [_fill_row("q1", clearing_price=None)])

    assert records[0].clearing_price is None
    depth = trade_through_depth(records)
    assert depth["count"] == 0 and depth["missing"] == 1


def test_cleared_fills_leaves_out_a_quote_that_never_filled():
    records = cleared_fills([_quote("q1"), _quote("q2")], [_fill_row("q1")])

    assert [r.order_id for r in records] == ["q1"]


def test_cleared_fills_falls_back_to_recorded_ts_when_the_venue_sent_none():
    records = cleared_fills([_quote("q1")],
                            [_fill_row("q1", venue_ts=None, recorded_ts=1_007_000)])

    assert records[0].filled_ts == 1007.0


def test_cleared_fills_keeps_a_missing_timestamp_missing():
    records = cleared_fills([_quote("q1", ts=None)],
                            [_fill_row("q1", venue_ts=None, recorded_ts=None)])

    assert records[0].placed_ts is None
    assert records[0].filled_ts is None
    assert fill_latency(records) == {"count": 0, "excluded": 1,
                                     "median_sec": None, "p90_sec": None}


def test_cleared_fills_empty_inputs_build_nothing():
    assert cleared_fills([], []) == []
    assert cleared_fills([_quote("q1")], []) == []


def test_cleared_fills_feeds_the_latency_metric_and_leaves_depth_unmeasured():
    quotes = [_quote("q1", ts=1000.0), _quote("q2", ts=1000.0)]
    fills = [_fill_row("q1", venue_ts=1_005_000), _fill_row("q2", venue_ts=1_015_000)]

    records = cleared_fills(quotes, fills)

    assert fill_latency(records)["median_sec"] == 10.0
    depth = trade_through_depth(records)
    assert depth["count"] == 0
    assert depth["missing"] == 2
    assert depth["median_cents"] is None
    assert depth["band"] == "unmeasured"

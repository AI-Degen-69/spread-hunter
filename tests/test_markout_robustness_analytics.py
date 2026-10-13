"""Post-fill markout and statistical robustness gates, in cents (#516).

Two halves of the same question, and neither is a chart. The first half asks whether the tape
moved against us after we were filled -- spread capture against toxic-flow absorption. The
second asks whether a positive mean is an edge or a small-sample accident.

This file holds the pure functions, and it follows the rule
`tests/test_statistical_analytics.py` and `tests/test_queue_realism_analytics.py` both hold:
**a measurement whose inputs were never recorded is `None`, never `0`.** An unmeasured excursion
and an excursion of zero are different claims, and a band nobody could judge reads `unmeasured`,
never `bad`.

T1 lands first: the 90/95 band on mean close PnL, moved into this module so there is one
definition of it. `kpi.py` imports it back, so the two callers cannot drift apart.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from core_brain import kpi
from core_brain import statistical_analytics as sa
from core_brain.statistical_analytics import (
    MARKOUT_BAND_WORDS,
    MARKOUT_HORIZONS,
    SQN_BAND_WORDS,
    excursion_summary,
    markout,
    markout_cents_band,
    max_adverse_excursion,
    signed_markout,
    sqn_band,
    system_quality_number,
)


def _row(fill_price: float = 0.50, size: float = 10.0, side: str = "BUY",
         **horizons) -> dict:
    """One fill's markout row, shaped like a `markouts` row the registry hands over."""
    row = {"fill_price": fill_price, "size": size, "side": side}
    row.update(horizons)
    return row


def _only(out: dict) -> dict:
    intervals = out["intervals"]
    assert len(intervals) == 1, intervals
    return intervals[0]


def _ci95(out: dict) -> dict:
    """The 95% band, selected by its own level rather than by position.

    `levels[-1]` is the 95% entry today because the helper's tuple is ordered 90 then 95. This
    helper reads the level it means, and `test_the_widest_band_is_the_95_one` pins that the last
    entry really is 95 -- so a future reorder fails loudly instead of silently mislabelling the
    90% band as 95%.
    """
    return next(level for level in out["levels"] if level["level"] == 95)


# --- T1: the 90/95 band on mean close PnL, in one place ----------------------


def test_the_band_frames_the_mean_on_a_ten_close_sample():
    out = sa.mean_pnl_ci([float(n) for n in range(1, 11)])

    assert out["mean_usd"] == pytest.approx(5.5)
    assert out["n"] == 10
    # df = 9, so the t multiplier is 2.262 -- not the normal 1.96.
    band = _ci95(out)
    assert band["lower"] == pytest.approx(3.334300, abs=1e-5)
    assert band["upper"] == pytest.approx(7.665700, abs=1e-5)
    assert band["includes_negative"] is False
    assert band["negative_depth_usd"] is None
    assert out["verdict"] == "positive"


def test_a_band_that_reaches_below_zero_says_so_and_how_deep():
    out = sa.mean_pnl_ci([1.0, -1.0] * 5)

    assert out["mean_usd"] == pytest.approx(0.0)
    band = _ci95(out)
    assert band["lower"] == pytest.approx(-0.754, abs=1e-3)
    assert band["upper"] == pytest.approx(0.754, abs=1e-3)
    assert band["includes_negative"] is True
    assert band["negative_depth_usd"] == pytest.approx(0.754, abs=1e-3)
    assert out["verdict"] == "spans_zero"


def test_a_hundred_closes_use_the_normal_multiplier():
    out = sa.mean_pnl_ci([1.0, -0.5] * 50)

    assert out["mean_usd"] == pytest.approx(0.25)
    band = _ci95(out)
    # df = 99 is past the t table, so `ci_critical_value` hands back the normal 1.96.
    assert band["lower"] == pytest.approx(0.102259, abs=1e-6)
    assert band["upper"] == pytest.approx(0.397741, abs=1e-6)
    assert out["verdict"] == "positive"


def test_a_tight_sample_still_gets_a_band_and_it_does_not_span_zero():
    out = sa.mean_pnl_ci([0.99, 1.01] * 5)

    band = _ci95(out)
    assert band["lower"] == pytest.approx(0.99246, abs=1e-5)
    assert band["upper"] == pytest.approx(1.00754, abs=1e-5)
    assert out["verdict"] == "positive"


def test_zero_variance_collapses_the_band_onto_the_mean():
    out = sa.mean_pnl_ci([2.0] * 5)

    band = _ci95(out)
    assert band["lower"] == 2.0
    assert band["upper"] == 2.0
    assert out["verdict"] == "positive"


def test_one_close_has_no_band_because_a_zero_would_read_as_breaking_even():
    out = sa.mean_pnl_ci([1.0])

    assert out["mean_usd"] == 1.0
    assert out["n"] == 1
    assert out["levels"] == []
    assert out["verdict"] is None


def test_no_closes_reports_no_mean_and_no_band():
    assert sa.mean_pnl_ci([]) == {"mean_usd": None, "n": 0, "levels": [], "verdict": None}


def test_the_widest_band_is_the_95_one():
    """The card reads the 95% entry by level; this pins that it exists."""
    out = sa.mean_pnl_ci([1.0, 2.0, 3.0])

    assert out["levels"][-1]["level"] == 95
    assert _ci95(out) is out["levels"][-1]


def test_there_is_one_definition_of_the_band_not_two():
    assert kpi._mean_pnl_ci is sa.mean_pnl_ci


# --- T2: signed post-fill markout, in cents ------------------------------------


def test_a_buy_that_ran_in_our_favour_reads_positive_cents_and_good():
    out = signed_markout([_row(mid_h0=0.51)])

    interval = _only(out)
    assert interval == {"horizon": "5m", "markout_cents": 2.0, "samples": 1,
                        "band": "good", "meaning": MARKOUT_BAND_WORDS["good"]}


def test_a_flat_mid_after_the_fill_reads_zero_and_still_good():
    interval = _only(signed_markout([_row(mid_h0=0.50)]))

    assert interval["markout_cents"] == 0.0
    assert interval["band"] == "good"


def test_a_buy_the_tape_ran_away_from_reads_negative_cents_and_bad():
    interval = _only(signed_markout([_row(mid_h0=0.49)]))

    assert interval["markout_cents"] == -2.0
    assert interval["band"] == "bad"
    assert interval["meaning"] == MARKOUT_BAND_WORDS["bad"]


def test_a_sell_is_signed_the_other_way_round():
    # A seller is hurt by the mid RISING and helped by it falling.
    assert _only(signed_markout([_row(side="SELL", mid_h0=0.51)]))["markout_cents"] == -2.0
    assert _only(signed_markout([_row(side="SELL", mid_h0=0.49)]))["markout_cents"] == 2.0


def test_the_figure_is_the_median_across_fills_not_a_size_weighted_mean():
    """The ticket's band rides on the MEDIAN; size carries no weight here.

    +2.0c on 10 shares and -2.0c on 30 shares: the weighted mean the chart above reports is
    -1.0c, and the median is 0.0c. Two different numbers, two different questions.
    """
    out = signed_markout([_row(size=10.0, mid_h0=0.51), _row(size=30.0, mid_h0=0.49)])

    interval = _only(out)
    assert interval["markout_cents"] == 0.0
    assert interval["markout_cents"] != -1.0
    assert interval["samples"] == 2


def test_a_row_whose_side_cannot_be_signed_is_skipped():
    assert signed_markout([_row(side="HOLD", mid_h0=0.51)])["intervals"] == []


def test_a_horizon_that_has_not_matured_is_omitted_not_zeroed():
    out = signed_markout([_row(mid_h0=0.51, mid_h3=None)])

    assert [i["horizon"] for i in out["intervals"]] == ["5m"]


def test_the_caller_can_narrow_the_horizons():
    out = signed_markout([_row(mid_h0=0.51, mid_h1=0.55)],
                         horizons=(("mid_h1", "1h"),))

    interval = _only(out)
    assert interval["horizon"] == "1h"
    assert interval["markout_cents"] == 10.0


def test_rows_with_no_usable_fill_or_mark_are_dropped():
    rows = [
        _row(mid_h0=None),                      # unmatured at this horizon
        _row(fill_price=None, mid_h0=0.51),     # no entry price to measure against
        _row(fill_price=0.0, mid_h0=0.51),      # dividing by zero would be invented
        _row(size=-5.0, mid_h0=0.51),           # a negative size is not a fill
        _row(mid_h0=float("nan")),              # non-finite is not a measurement
        _row(mid_h0=0.51),                      # the only usable row
    ]

    interval = _only(signed_markout(rows))
    assert interval["samples"] == 1


def test_a_row_with_no_recorded_size_still_counts_once():
    """`size = _finite(size) or 1.0` is the sibling chart's own rule, kept here.

    A zero or missing size takes unit weight rather than being dropped -- it is an unrecorded
    field, not a zero-size fill. Only a NEGATIVE size is skipped. For a median the weight is
    irrelevant, so this pins the row COUNT and nothing else.
    """
    assert _only(signed_markout([_row(size=0.0, mid_h0=0.51)]))["samples"] == 1
    assert _only(signed_markout([_row(size=None, mid_h0=0.51)]))["samples"] == 1


def test_the_band_edges_are_the_ticket_table():
    assert markout_cents_band(0.0) == "good"
    assert markout_cents_band(0.01) == "good"
    assert markout_cents_band(-0.4) == "average"
    assert markout_cents_band(-1.0) == "bad"
    assert markout_cents_band(-4.0) == "bad"
    # An unmeasured figure is not a bad one.
    assert markout_cents_band(None) == "unmeasured"
    assert markout_cents_band(float("nan")) == "unmeasured"


def test_a_store_with_no_rows_reads_unmeasured_rather_than_flat():
    assert signed_markout([]) == {"intervals": []}


def test_the_unsigned_chart_reading_is_unchanged():
    """`markout()` still reports the size-weighted unsigned displacement in bps.

    The two readers share a loop now, so this is the pin that the older one did not move: a
    SELL row's raw displacement is reported unsigned here and signed in `signed_markout`.
    """
    row = _row(side="SELL", mid_h0=0.51)

    chart = _only(markout([row]))
    assert chart["displacement_bps"] == 200.0
    assert chart["samples"] == 1
    assert "markout_cents" not in chart
    assert signed_markout([row])["intervals"][0]["markout_cents"] == -2.0


def test_the_shared_loop_reports_every_matured_horizon_in_chart_order():
    out = signed_markout([_row(mid_h0=0.51, mid_h3=0.52, mid_h1=0.53, mid_h2=0.54)])

    assert [i["horizon"] for i in out["intervals"]] == [label for _, label in MARKOUT_HORIZONS]


# --- T3: maximum adverse excursion, over the holding window -----------------------


def _close_row(condition_id: str = "cid-1", ts: float | None = 1300.0,
               realized_pnl: float | None = None, method: str = "merge") -> dict:
    row = {"condition_id": condition_id, "ts": ts, "method": method}
    if realized_pnl is not None:
        row["realized_pnl"] = realized_pnl
    return row


def _mark(condition_id: str = "cid-1", ts: float = 1000.0, fill_price: float = 0.50,
          side: str = "BUY", **horizons) -> dict:
    """One `markouts` row: an entry fill and its recorded post-fill mids."""
    row = {"condition_id": condition_id, "ts": ts, "fill_price": fill_price,
           "side": side, "size": 10.0}
    row.update(horizons)
    return row


def test_a_buy_that_dipped_below_the_entry_reports_the_worst_move_in_cents():
    out = max_adverse_excursion(0.50, "BUY", [0.52, 0.47, 0.49, 0.53])

    assert out["mae_cents"] == pytest.approx(3.0)
    assert out["worst_price"] == 0.47
    assert out["samples"] == 4


def test_a_sell_is_measured_against_the_high_side_instead():
    out = max_adverse_excursion(0.50, "SELL", [0.48, 0.54, 0.51])

    assert out["mae_cents"] == pytest.approx(4.0)
    assert out["worst_price"] == 0.54


def test_a_position_the_tape_never_ran_against_measures_zero_not_missing():
    # Zero here is a real reading: we looked, and nothing went against us.
    assert max_adverse_excursion(0.50, "BUY", [0.50, 0.51, 0.52])["mae_cents"] == 0.0
    assert max_adverse_excursion(0.50, "BUY", [0.50, 0.50])["mae_cents"] == 0.0


def test_non_finite_marks_are_dropped_rather_than_sorted():
    out = max_adverse_excursion(0.50, "BUY", [0.52, float("nan"), 0.47])

    assert out["samples"] == 2
    assert out["mae_cents"] == pytest.approx(3.0)


def test_an_unusable_series_entry_or_side_is_none_never_zero():
    assert max_adverse_excursion(0.50, "BUY", [])["mae_cents"] is None
    assert max_adverse_excursion(0.0, "BUY", [0.47])["mae_cents"] is None
    assert max_adverse_excursion(None, "BUY", [0.47])["mae_cents"] is None
    assert max_adverse_excursion(0.50, "HOLD", [0.47])["mae_cents"] is None
    assert max_adverse_excursion(0.50, "BUY", [float("nan")])["mae_cents"] is None
    assert max_adverse_excursion(0.0, "BUY", [0.47])["worst_price"] is None


def test_a_mark_at_the_close_instant_is_inside_the_window():
    """`<=`, not `<`: the mark and the close at the same instant are one moment."""
    row = _mark(mid_h0=0.47)                      # stamped 1000 + 300 = 1300

    at_close = excursion_summary([_close_row(ts=1300.0)], [row])
    one_second_earlier = excursion_summary([_close_row(ts=1299.0)], [row])

    assert at_close["measured"] == 1
    assert at_close["median_cents"] == pytest.approx(3.0)
    assert one_second_earlier["measured"] == 0
    assert one_second_earlier["no_marks_in_window"] == 1


def test_only_the_marks_inside_the_holding_window_are_measured():
    row = _mark(mid_h0=0.47, mid_h3=0.46)         # stamped 1300 and 1900

    later = excursion_summary([_close_row(ts=1900.0)], [row])
    earlier = excursion_summary([_close_row(ts=1300.0)], [row])

    # The position held to 1900 saw both marks, so the worst move is the deeper one.
    assert later["median_cents"] == pytest.approx(4.0)
    # Held only to 1300, the 15-minute mark is after the exit and measures nothing.
    assert earlier["median_cents"] == pytest.approx(3.0)


def test_every_markout_row_lands_in_exactly_one_bucket():
    rows = [
        _mark(condition_id="cid-open", ts=2000.0, mid_h0=0.47),      # no close at all
        _mark(condition_id="cid-1", side="SELL", mid_h0=0.47),       # an exit fill, not a position
        _mark(condition_id="cid-gap", ts=5000.0, mid_h0=0.47),       # closed before its first mark
        _mark(condition_id="cid-1", ts=None, mid_h0=0.47),           # unrecorded fill time
        _mark(condition_id="cid-1", mid_h0=0.47),                    # the one measurable row
    ]
    closes = [_close_row("cid-1", ts=1300.0), _close_row("cid-gap", ts=5100.0)]

    out = excursion_summary(closes, rows)

    assert out["measured"] == 1
    assert out["open"] == 1
    assert out["exit_fill"] == 1
    assert out["no_marks_in_window"] == 1
    assert out["not_recorded"] == 1
    assert (out["measured"] + out["open"] + out["exit_fill"]
            + out["no_marks_in_window"] + out["not_recorded"]) == len(rows)


def test_the_close_that_bounds_a_fill_is_the_first_one_after_it():
    """A mid is its own close; the window starts at the NEXT close, not at the mid.

    `_mark(ts=2000)` with closes at 500, 1900 and 2400 has no mark inside a window bounded by
    1900 (that close predates the fill), so the bounding close is 2400.
    """
    row = _mark(ts=2000.0, mid_h0=0.47)            # stamped 2300
    closes = [_close_row("cid-1", ts=500.0), _close_row("cid-1", ts=1900.0),
              _close_row("cid-1", ts=2400.0)]

    out = excursion_summary(closes, [row])

    assert out["measured"] == 1
    assert out["median_cents"] == pytest.approx(3.0)


def test_the_summary_is_a_median_and_a_p90_across_positions():
    rows = [_mark("cid-1", mid_h0=0.49),           # 1.0c
            _mark("cid-2", mid_h0=0.47),           # 3.0c
            _mark("cid-3", mid_h0=0.40)]           # 10.0c
    closes = [_close_row(cid, ts=1300.0) for cid in ("cid-1", "cid-2", "cid-3")]

    out = excursion_summary(closes, rows)

    # Interpolated, so p50 is `statistics.median` and p90 is between the top two.
    assert out["median_cents"] == pytest.approx(3.0)
    assert out["p90_cents"] == pytest.approx(8.6)


def test_a_store_with_nothing_to_measure_reports_none_and_no_band_key():
    out = excursion_summary([], [])

    assert out["median_cents"] is None
    assert out["p90_cents"] is None
    assert out["measured"] == 0
    # Descriptive only: "6c of excursion" has no verdict without a stop distance to judge it by.
    assert "band" not in out


def test_the_horizon_seconds_mirror_the_configured_horizons():
    """The window arithmetic is only honest if these offsets are the sampler's own.

    They are declared rather than imported, because `core_brain.markout` loads config and pulls
    in the order registry at import time. This pin is what keeps the copy truthful: move
    `markout_horizons` in config and this fails instead of silently mis-windowing every MAE.
    """
    from core_brain.config import load as load_cfg

    horizons = load_cfg().markout_horizons

    assert sa.MARKOUT_HORIZON_SECONDS == {
        "mid_h0": float(horizons[0]), "mid_h1": float(horizons[1]),
        "mid_h2": float(horizons[2]), "mid_h3": float(horizons[3]),
    }


# --- T3: system quality number ----------------------------------------------------


def test_a_steady_winner_reads_as_a_stable_edge():
    out = system_quality_number([float(n) for n in range(1, 11)])

    assert out["sqn"] == pytest.approx(5.744563, abs=1e-6)
    assert out["band"] == "good"
    assert out["meaning"] == SQN_BAND_WORDS["good"]
    assert out["n"] == 10
    assert out["mean_usd"] == pytest.approx(5.5)
    assert out["stdev_usd"] == pytest.approx(3.027650, abs=1e-6)


def test_a_lottery_ticket_reads_as_noise():
    assert system_quality_number([3.0, -1.0] * 5)["sqn"] == pytest.approx(1.5)
    assert system_quality_number([3.0, -1.0] * 5)["band"] == "bad"
    # 0.523678, not the 0.523681 the plan carried over from the intake comment: that figure was
    # rounded from a 6-decimal stdev. The score is computed from the full-precision one.
    assert system_quality_number([1.0, -0.9] * 50)["sqn"] == pytest.approx(0.523678, abs=1e-6)
    assert system_quality_number([1.0, -0.9] * 50)["band"] == "bad"


def test_a_promising_but_unproven_sample_sits_in_the_middle_band():
    assert system_quality_number([4.0, -1.0] * 5)["sqn"] == pytest.approx(1.8)
    assert system_quality_number([4.0, -1.0] * 5)["band"] == "average"


def test_the_two_fixed_boundaries_count_as_promising():
    """`< 1.6` is noise and `> 2.5` is stable, so 1.6 and 2.5 themselves are neither."""
    assert system_quality_number([23.0, -7.0] * 5)["sqn"] == 1.6
    assert system_quality_number([23.0, -7.0] * 5)["band"] == "average"
    assert system_quality_number([11.0, -1.0] * 5)["sqn"] == 2.5
    assert system_quality_number([11.0, -1.0] * 5)["band"] == "average"


def test_a_larger_sample_of_the_same_shape_scores_higher():
    out = system_quality_number([1.0, -0.5] * 50)

    assert out["sqn"] == pytest.approx(3.316625, abs=1e-6)
    assert out["band"] == "good"
    assert out["n"] == 100


def test_a_tight_sample_of_tiny_wins_still_scores_high():
    out = system_quality_number([0.99, 1.01] * 5)

    assert out["sqn"] == pytest.approx(300.0)
    assert out["band"] == "good"


def test_no_spread_or_no_sample_is_none_never_a_confident_verdict():
    flat = system_quality_number([2.0] * 5)
    assert flat["sqn"] is None
    assert flat["band"] == "unmeasured"
    assert flat["stdev_usd"] == 0.0        # measured: there was no spread
    assert flat["n"] == 5

    one = system_quality_number([1.0])
    assert one["sqn"] is None
    assert one["stdev_usd"] is None        # not measured: one observation has no spread

    none = system_quality_number([])
    assert none["n"] == 0
    assert none["sqn"] is None
    assert none["mean_usd"] is None


def test_non_finite_pnls_are_dropped_before_the_score():
    out = system_quality_number([1.0, float("nan"), -1.0, float("inf")])

    assert out["n"] == 2
    assert out["sqn"] == pytest.approx(0.0)


def test_the_sqn_band_is_classified_on_the_rounded_score():
    assert sqn_band(2.500001) == "good"
    assert sqn_band(2.5) == "average"
    assert sqn_band(1.6) == "average"
    assert sqn_band(1.599999) == "bad"
    assert sqn_band(None) == "unmeasured"


def test_importing_this_module_pulls_in_neither_the_registry_nor_kpi():
    """The pure module's promise: no import side effects.

    `kpi` loads config and writes a session file when imported, and the registry opens SQLite.
    A module-level import of either from here would drag both into every reader of the analytics.
    """
    code = (
        "import sys\n"
        "import core_brain.statistical_analytics\n"
        "assert 'core_brain.kpi' not in sys.modules, 'kpi loaded'\n"
        "assert 'core_brain.order_registry' not in sys.modules, 'registry loaded'\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr

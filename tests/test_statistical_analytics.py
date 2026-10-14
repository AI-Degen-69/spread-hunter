"""The payload the analytics charts read (#134).

Five charts on the Performance & Analytics tab read `kpi.statistical_analytics`
and nothing ever produced it — not `kpi.report`, not the TS bridge, at any
commit. So they have shown "unmeasured" since they were written, including on a
run that merged a pair at a profit.

The rule these tests exist to hold: **a section with no input is omitted, not
synthesised.** A chart saying "unmeasured" is information. A chart drawing a
smooth curve through one observation is a lie that looks like analysis.
"""
from __future__ import annotations

import math
import re

import pytest

from core_brain.statistical_analytics import (
    MIN_MC_SAMPLES,
    build,
    markout,
    monte_carlo,
    pair_costs,
    position_returns,
    probability_bell,
)


def _close(pnl: float, cost: float = 5.70, shares: float = 6.0,
           method: str = "shadow_merge") -> dict:
    return {"ts": 1_788_000_000.0, "condition_id": "0xm", "market_slug": "m",
            "method": method, "shares": shares, "cost_basis": cost,
            "proceeds": cost + pnl, "realized_pnl": pnl}


# --- position returns -------------------------------------------------------

def test_a_merged_pair_becomes_one_observation():
    # Arrange — the real close from shadow-05: 6 shares, $5.70 in, $6.00 out.
    closes = [_close(0.30)]

    # Act
    out = position_returns(closes)

    # Assert
    assert len(out["positions"]) == 1
    pos = out["positions"][0]
    assert pos["type"] == "MERGED_PAIR"
    assert pos["pnl_usd"] == pytest.approx(0.30)
    assert pos["pnl_pct"] == pytest.approx(100 * 0.30 / 5.70)


def test_return_is_measured_against_the_positions_own_cost():
    # Arrange — a 5c edge on a 95c pair is ~5.3%, not diluted by bankroll size.
    out = position_returns([_close(0.05, cost=0.95, shares=1.0)])

    # Act / Assert
    assert out["positions"][0]["pnl_pct"] == pytest.approx(5.263157, rel=1e-4)


def test_an_unwind_is_not_counted_as_a_merged_pair():
    # Arrange — the distribution chart splits merged from unwound.
    out = position_returns([_close(-0.10, method="sell")])

    # Act / Assert
    assert out["positions"][0]["type"] == "UNWIND"


def test_a_close_with_no_cost_basis_keeps_its_dollars_and_drops_its_percent():
    # Arrange — dividing by zero to fill a column would put a fabricated
    # number in the distribution the operator judges the strategy on.
    out = position_returns([_close(0.30, cost=0.0)])

    # Act / Assert
    assert out["positions"][0]["pnl_usd"] == pytest.approx(0.30)
    assert out["positions"][0]["pnl_pct"] is None


def test_a_single_observation_has_a_mean_and_no_spread():
    # Arrange — reporting 0.0 deviation on a sample of one reads as
    # "perfectly consistent", which is the opposite of what it means.
    out = position_returns([_close(0.30)])

    # Act / Assert
    assert out["mean_pnl_usd"] == pytest.approx(0.30)
    assert out["stdev_pnl_usd"] is None
    assert out["sem_pnl_usd"] is None


def test_spread_appears_once_there_is_a_second_observation():
    # Arrange
    out = position_returns([_close(0.30), _close(0.10)])

    # Act / Assert
    assert out["stdev_pnl_usd"] is not None
    # Both figures are rounded for the wire, so compare to that precision.
    assert out["sem_pnl_usd"] == pytest.approx(
        out["stdev_pnl_usd"] / math.sqrt(2), abs=1e-6)


def test_no_closes_is_an_empty_distribution_not_a_zero_one():
    # Arrange / Act
    out = position_returns([])

    # Assert
    assert out["positions"] == []
    assert "mean_pnl_usd" not in out


# --- pair costs -------------------------------------------------------------

def test_pair_cost_is_the_per_share_assembly_price():
    # Arrange — $5.70 for 6 shares is a 95c pair.
    out = pair_costs([_close(0.30)])

    # Act / Assert
    assert out["samples_count"] == 1
    assert out["mean"] == pytest.approx(0.95)
    assert out["min_observed"] == pytest.approx(0.95)


def test_an_unwind_never_enters_the_pair_cost_density():
    # Arrange — an unwind did not assemble a pair at a price.
    out = pair_costs([_close(0.30, method="sell")])

    # Act / Assert
    assert out["samples_count"] == 0
    assert out["bins"] == []


def test_the_pair_cost_bins_frame_the_dollar_the_instrument_pays():
    # Arrange
    out = pair_costs([_close(0.30)])

    # Act / Assert
    profit_bins = [b for b in out["bins"] if b["status"] == "profit"]
    assert profit_bins[0]["min"] == pytest.approx(0.90)
    assert profit_bins[-1]["max"] == pytest.approx(1.00)
    # Plus the overflow bin for anything at or above the dollar.
    assert out["bins"][-1]["status"] == "loss"
    assert sum(b["count"] for b in out["bins"]) == 1


# --- probability bell -------------------------------------------------------

def test_the_bell_is_built_from_executed_prices():
    # Arrange — what filled, not what we asked for.
    fills = [{"price": 0.70}, {"price": 0.25}]

    # Act
    out = probability_bell(fills)

    # Assert
    assert out["samples_count"] == 2
    assert out["sweet_spot_pct"] == pytest.approx(100.0)


def test_fills_outside_the_band_lower_the_sweet_spot_share():
    # Arrange
    out = probability_bell([{"price": 0.05}, {"price": 0.50}])

    # Act / Assert
    assert out["sweet_spot_pct"] == pytest.approx(50.0)


def test_one_price_has_no_curve_to_fit():
    # Arrange — a normal curve needs a spread. Drawing one anyway would be a
    # shape invented from a single point.
    out = probability_bell([{"price": 0.50}])

    # Act / Assert
    assert all(b["theoretical_pdf"] is None for b in out["bins"])


def test_no_fills_is_an_unmeasured_bell():
    # Arrange / Act
    out = probability_bell([])

    # Assert
    assert out["bins"] == []
    assert out["sweet_spot_pct"] is None


# --- markout ----------------------------------------------------------------

def test_markout_reports_displacement_in_bps_of_the_fill():
    # Arrange — filled at 0.50, mid at 0.51 five minutes later: +200 bps.
    rows = [{"fill_price": 0.50, "size": 10.0, "mid_h0": 0.51}]

    # Act
    out = markout(rows)

    # Assert
    assert out["intervals"][0]["horizon"] == "5m"
    assert out["intervals"][0]["displacement_bps"] == pytest.approx(200.0)
    assert out["intervals"][0]["samples"] == 1


def test_markout_is_size_weighted():
    # Arrange — a 90-share fill that drifted and a 10-share one that did not.
    rows = [{"fill_price": 0.50, "size": 90.0, "mid_h0": 0.51},
            {"fill_price": 0.50, "size": 10.0, "mid_h0": 0.50}]

    # Act
    out = markout(rows)

    # Assert
    assert out["intervals"][0]["displacement_bps"] == pytest.approx(180.0)


def test_an_unmatured_horizon_is_omitted_not_reported_as_zero():
    # Arrange — no horizon has matured. Zero bps would report "no adverse
    # selection" for a measurement that has not happened.
    rows = [{"fill_price": 0.50, "size": 10.0}]

    # Act
    out = markout(rows)

    # Assert
    assert out["intervals"] == []


# --- monte carlo ------------------------------------------------------------

def test_a_thin_sample_produces_no_fan_at_all():
    # Arrange — resampling a handful of closes draws a fan whose width is a
    # property of the bootstrap, not of the strategy.
    assert monte_carlo([0.30] * (MIN_MC_SAMPLES - 1), 100.0) is None


def test_a_sufficient_sample_produces_an_ordered_percentile_fan():
    # Arrange
    out = monte_carlo([0.30, 0.10, -0.05, 0.20, 0.15, 0.25], 100.0,
                      paths=200, cycles=10)

    # Assert
    assert out is not None
    assert out["steps"][0]["p50"] == pytest.approx(100.0)
    for step in out["steps"]:
        assert step["p01"] <= step["p10"] <= step["p50"] <= step["p90"] <= step["p99"]


def test_the_fan_is_reproducible_for_the_same_registry():
    # Arrange — an envelope that shifts on every poll reads as new information
    # when nothing changed.
    sample = [0.30, 0.10, -0.05, 0.20, 0.15, 0.25]

    # Act
    first = monte_carlo(sample, 100.0, paths=100, cycles=5)
    second = monte_carlo(sample, 100.0, paths=100, cycles=5)

    # Assert
    assert first == second


def test_no_capital_means_no_simulation():
    # Arrange / Act / Assert — a percentage of zero is undefined, not 0%.
    assert monte_carlo([0.1] * 10, 0.0) is None


# --- the whole payload ------------------------------------------------------

def test_build_populates_every_section_it_has_input_for():
    # Arrange — the shadow-05 shape: one merged pair, two fills, no markouts.
    payload = build([_close(0.30)], [{"price": 0.70}, {"price": 0.25}], [], 85.42)

    # Assert
    assert len(payload["position_returns"]["positions"]) == 1
    assert payload["pair_costs"]["samples_count"] == 1
    assert payload["probability_bell"]["samples_count"] == 2
    assert payload["markout"]["intervals"] == []
    # One close is not a distribution.
    assert "monte_carlo" not in payload
    # The fill-path split is always present: it is derived from the closes the
    # payload already holds, not from a sample large enough to draw.
    assert payload["fill_path_edge"]["merge_count"] >= 1
    # The friction section reads those same closes (#514). This run's only close is a
    # merge, so its rescue population is empty and every mean says so. `0.0` here would
    # claim a bailout cost nothing.
    friction = payload["rescue_friction"]
    assert friction["rescue_count"] == 0
    assert friction["non_rescue_count"] == 1
    assert friction["mean_sled_cents"] is None
    assert friction["bands"]["sled"] == "unmeasured"
    # The queue-realism section (#515) is always present as well, and this call passes no
    # quotes, so nothing is measurable. The section says that instead of a zero depth.
    realism = payload["queue_realism"]
    assert realism["cleared_fill_count"] == 0
    assert realism["trade_through_depth"]["count"] == 0
    assert realism["trade_through_depth"]["median_cents"] is None
    assert realism["trade_through_depth"]["band"] == "unmeasured"
    assert realism["fill_latency"]["median_sec"] is None
    # The robustness gates (#516) read those same closes plus the markout rows. One close is a
    # single observation, so the band says unmeasured instead of breaks-even; this call passes no
    # markouts and no quotes, so the markout half is absent and the MAE half counts nothing.
    robustness = payload["robustness"]
    assert robustness["markout"]["intervals"] == []
    assert robustness["mae"]["measured"] == 0
    assert robustness["mae"]["median_cents"] is None
    assert robustness["mean_pnl_ci"]["n"] == 1
    assert robustness["mean_pnl_ci"]["band"] == "unmeasured"
    assert robustness["sqn"]["band"] == "unmeasured"
    # The capital-risk section (#517) reads those same closes as percentages, and takes its equity
    # curve from the caller. This call passes no curve, so the drawdown half measures nothing; one
    # close is a single observation, so both banded readings say unmeasured rather than a verdict.
    capital = payload["capital_risk"]
    assert capital["pnl_skewness"]["n"] == 1
    assert capital["pnl_skewness"]["band"] == "unmeasured"
    assert capital["sortino_ratio"]["band"] == "unmeasured"
    assert capital["drawdown_recovery"]["deepest_usd"] is None
    assert capital["drawdown_recovery"]["episodes"] == 0


def test_build_on_an_empty_run_synthesises_nothing():
    # Arrange / Act
    payload = build([], [], [], 100.0)

    # Assert — every chart reads "unmeasured", which is the honest answer.
    assert payload["position_returns"]["positions"] == []
    assert payload["pair_costs"]["bins"] == []
    assert payload["probability_bell"]["bins"] == []
    assert payload["markout"]["intervals"] == []
    assert "monte_carlo" not in payload
    # The split is still present, and unmeasured rather than zero: `0.0` here
    # would read as "rescues cost nothing".
    edge = payload["fill_path_edge"]
    assert edge["merge_expectancy_usd"] is None
    assert edge["rescue_rate"] is None
    assert edge["net_expectancy_usd"] is None
    # Same rule for the friction means: the section is present and says unmeasured.
    friction = payload["rescue_friction"]
    assert friction["rescue_count"] == 0
    assert friction["mean_sled_cents"] is None
    assert friction["mean_tcdr"] is None
    assert all(band == "unmeasured" for band in friction["bands"].values())
    # And the queue-realism section, shape-frozen: a caller reading it must never branch
    # on whether the key is there, even on a run with nothing in it.
    assert payload["queue_realism"] == {
        "cleared_fill_count": 0,
        "trade_through_depth": {"count": 0, "missing": 0, "not_trade_through": 0,
                                "median_cents": None, "p75_cents": None,
                                "band": "unmeasured"},
        "fill_latency": {"count": 0, "excluded": 0,
                         "median_sec": None, "p90_sec": None},
    }
    # And the robustness section (#516), same rule: present with every figure unmeasured, because
    # a zero here would read as "no adverse selection", "no excursion" and "no edge" on a run
    # that has measured none of the three.
    robustness = payload["robustness"]
    assert robustness["units"] == "cents_per_share"
    assert robustness["markout"]["intervals"] == []
    assert robustness["mae"]["measured"] == 0
    assert robustness["mae"]["median_cents"] is None
    assert robustness["mean_pnl_ci"]["n"] == 0
    assert robustness["mean_pnl_ci"]["mean_cents"] is None
    assert robustness["mean_pnl_ci"]["lower_cents"] is None
    assert robustness["mean_pnl_ci"]["band"] == "unmeasured"
    assert robustness["sqn"]["n"] == 0
    assert robustness["sqn"]["sqn"] is None
    assert robustness["sqn"]["band"] == "unmeasured"
    # And the capital-risk section (#517), shape-frozen the same way: present, every reading
    # unmeasured, and the seconds key absent because there was no drawdown to time.
    assert payload["capital_risk"] == {
        "units": "percent_per_close_and_curve_points",
        "pnl_skewness": {"pnl_skewness": None, "band": "unmeasured",
                         "meaning": "fewer than three closes, or no spread to divide by", "n": 0},
        "sortino_ratio": {"sortino_ratio": None, "risk_free_pct_per_close": 0.0,
                          "band": "unmeasured",
                          "meaning": "fewer than two closes, no spread, or no downside to measure",
                          "n": 0},
        "drawdown_recovery": {
            "units": "curve_points_and_seconds", "episodes": 0, "open": 0, "skipped": 0,
            "deepest_usd": None, "deepest_pct": None, "deepest_recovered": None,
            "drawdown_recovery_trades": {"median": None, "p90": None, "n": 0},
        },
    }


def test_the_equity_curve_moves_only_the_capital_risk_section():
    """`build()` gains one keyword and one key; every sibling section keeps its own shape."""
    # Arrange — a curve that fell 200 and regained the old peak, and one close to read.
    points = [{"v": v} for v in (1100.0, 900.0, 950.0, 1100.0, 1200.0)]
    closes = [_close(0.30)]

    # Act
    bare = build(closes, [], [], 100.0)
    loaded = build(closes, [], [], 100.0, equity_curve=points)

    # Assert — the new half reads the curve...
    assert bare["capital_risk"]["drawdown_recovery"]["deepest_usd"] is None
    assert loaded["capital_risk"]["drawdown_recovery"]["deepest_usd"] == pytest.approx(200.0)
    assert loaded["capital_risk"]["drawdown_recovery"]["deepest_pct"] == pytest.approx(18.181818,
                                                                                       abs=1e-6)
    # ...and only the new half moved: the closes half is identical, and so is every sibling.
    assert loaded["capital_risk"]["pnl_skewness"] == bare["capital_risk"]["pnl_skewness"]
    for section in ("position_returns", "closed_positions", "pair_costs", "fill_path_edge",
                    "rescue_friction", "queue_realism", "robustness", "probability_bell",
                    "markout"):
        assert bare[section] == loaded[section], section


def test_build_reads_waits_from_the_quotes_it_is_given():
    # Arrange -- a quote that rested ten seconds and the fill that credited it, in the
    # registry's own units: `ts` in seconds, `venue_ts` in milliseconds.
    quotes = [{"ts": 1000.0, "local_id": "q1", "price": 0.50}]
    fills = [{"order_uuid": "q1", "venue_ts": 1_010_000, "size": 5.0}]

    # Act
    payload = build([], fills, [], 100.0, quotes)

    # Assert -- the wait is measurable from rows the registry already holds, while the
    # depth is not: nothing anywhere records the print that cleared the level, so the row
    # counts as missing rather than as a depth of zero.
    realism = payload["queue_realism"]
    assert realism["cleared_fill_count"] == 1
    assert realism["fill_latency"]["median_sec"] == 10.0
    assert realism["fill_latency"]["p90_sec"] == 10.0
    assert realism["trade_through_depth"]["count"] == 0
    assert realism["trade_through_depth"]["missing"] == 1
    assert realism["trade_through_depth"]["median_cents"] is None
    assert realism["trade_through_depth"]["band"] == "unmeasured"


def test_closed_positions_mirrors_the_distribution_sample():
    # Arrange — the banner counts observations off this key.
    payload = build([_close(0.30), _close(0.10, method="sell")], [], [], 100.0)

    # Act / Assert
    assert payload["closed_positions"] == payload["position_returns"]["positions"]
    assert len(payload["closed_positions"]) == 2


# --- what the chart actually renders ----------------------------------------
# Driven through the real app.js under node, because the defect this section
# guards against lived in the renderer, not in the payload: with the spread
# omitted, the chart substituted hardcoded constants (0.42 / 0.102) and
# declared "EDGE CONFIRMED" off a single trade.

import json
import shutil
import subprocess
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "js" / "position_dist_harness.cjs"
_APP_JS = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "app.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _render(stats: dict, metric: str | None = None, ci_level: int | None = None) -> dict:
    payload: dict = {"statistical_analytics": stats}
    if metric is not None:
        payload["metric"] = metric
    if ci_level is not None:
        payload["ciLevel"] = ci_level
    out = subprocess.run(
        [shutil.which("node"), str(HARNESS), json.dumps(payload)],
        capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


@requires_node
def test_one_observation_never_reads_as_a_confirmed_edge():
    # Arrange — the real shadow-05 close, and nothing else.
    stats = build([_close(0.30)], [{"price": 0.70}, {"price": 0.25}], [], 85.42)

    # Act
    rendered = _render(stats)

    # Assert
    assert "EDGE CONFIRMED" not in rendered["badge"]
    assert "unmeasured" in rendered["badge"]
    assert "NEEDS 2+" in rendered["badge"]
    assert "ACCUMULATING" in rendered["footer"]


@requires_node
def test_the_chart_draws_once_there_is_an_observation():
    # Arrange — the panel used to say "No closed positions recorded" on a run
    # that had merged a pair at a profit, because nothing produced the payload.
    stats = build([_close(0.30)], [], [], 85.42)

    # Act
    rendered = _render(stats)

    # Assert
    assert rendered["chartEmpty"] is False
    assert "1 Observations" in rendered["banner"]
    assert "1 Merged" in rendered["banner"]


@requires_node
def test_a_real_sample_does_produce_an_interval():
    # Arrange — six closes: now there is a spread to state.
    closes = [_close(p) for p in (0.30, 0.25, 0.35, 0.20, 0.28, 0.31)]
    stats = build(closes, [], [], 85.42)

    # Act
    rendered = _render(stats)

    # Assert
    assert "unmeasured" not in rendered["badge"]
    assert "%" in rendered["badge"]


@requires_node
def test_an_empty_run_still_says_so():
    # Arrange / Act
    rendered = _render(build([], [], [], 85.42))

    # Assert
    assert rendered["chartEmpty"] is True


def test_the_renderer_carries_no_invented_statistics():
    # Arrange — the constants that produced a confident interval from nothing.
    source = _APP_JS.read_text(encoding="utf-8")

    # Act / Assert
    for invented in (": 0.42", ": 0.102", ": 0.042", "|| 0.981", "|| 0.008",
                     "|| 0.945", "|| 17",
                     # The outcome-probability arm's bars came from a row's
                     # position in the list, and its axis from a fixed 45pt.
                     "i * 17 + 23", "delta = 45.0"):
        assert invented not in source, f"invented statistic back in app.js: {invented}"


# --- outcome-probability arm (#538) -----------------------------------------

def _fills(*prices: float) -> list[dict]:
    return [{"price": p} for p in prices]


def _bar_counts(rendered: dict) -> list[int]:
    # Bar titles read "low% to high%: N fills" once the arm measures.
    return [int(m) for m in re.findall(r": (\d+) fills", rendered["svgText"])]


@requires_node
def test_the_probability_arm_draws_only_measured_fill_counts():
    # Arrange — six fills across two bins, three closes (a number the arm
    # must never draw from again).
    fills = _fills(0.45, 0.45, 0.47, 0.47, 0.50, 0.50)
    closes = [_close(0.30), _close(-0.10, method="sell"),
              _close(0.05, cost=0.95, shares=1.0)]
    stats = build(closes, fills, [], 85.42)

    # Act
    rendered = _render(stats, metric="outcome_prob")

    # Assert — every drawn bar count is a measured fill, summing to the sample.
    assert _bar_counts(rendered) == [4, 2]
    assert sum(_bar_counts(rendered)) == stats["probability_bell"]["samples_count"]


@requires_node
def test_fills_without_closes_still_draw_the_probability_arm():
    # Arrange — a run that filled but never closed: the arm measures fills,
    # so "No closed positions recorded" must not gate it.
    stats = build([], _fills(0.45, 0.50, 0.47), [], 85.42)

    # Act
    rendered = _render(stats, metric="outcome_prob")

    # Assert
    assert rendered["chartEmpty"] is False
    assert sum(_bar_counts(rendered)) == 3


@requires_node
def test_closes_without_fills_leave_the_probability_arm_unmeasured():
    # Arrange
    stats = build([_close(0.30)], [], [], 85.42)

    # Act
    rendered = _render(stats, metric="outcome_prob")

    # Assert
    assert "unmeasured" in rendered["badge"]
    assert _bar_counts(rendered) == []


@requires_node
def test_identical_fill_prices_draw_one_bar_and_no_curve():
    # Arrange — four fills at one price: one bar, and no normal curve fitted
    # to a zero spread (which used to render as NaN geometry).
    stats = build([_close(0.30)], _fills(0.47, 0.47, 0.47, 0.47), [], 85.42)

    # Act
    rendered = _render(stats, metric="outcome_prob")

    # Assert
    assert _bar_counts(rendered) == [4]
    assert "NaN" not in rendered["chart"]
    assert "Infinity" not in rendered["chart"]


@requires_node
def test_the_probability_arm_ignores_the_close_count():
    # Arrange — the same fills, a different number of closes. Bars, badge
    # and footer describe fills, so nothing on the arm may move.
    fills = _fills(0.45, 0.50, 0.47)
    one = _render(build([_close(0.30)], fills, [], 85.42), metric="outcome_prob")
    three = _render(build([_close(0.30), _close(0.10), _close(-0.05, method="sell")],
                          fills, [], 85.42), metric="outcome_prob")

    # Assert
    assert re.findall(r"<title>([^<]*fills[^<]*)</title>", one["chart"]) \
        == re.findall(r"<title>([^<]*fills[^<]*)</title>", three["chart"])
    assert one["badge"] == three["badge"]
    assert one["footer"] == three["footer"]


@requires_node
def test_the_probability_arm_states_an_interval_not_an_edge():
    # Arrange
    fills = _fills(0.44, 0.46, 0.48, 0.50, 0.45, 0.47)
    stats = build([_close(0.30)], fills, [], 85.42)

    # Act
    rendered = _render(stats, metric="outcome_prob")

    # Assert — a fill-price distribution has no "edge above zero" to confirm.
    assert "EDGE CONFIRMED" not in rendered["badge"]
    assert "ZERO CROSSING" not in rendered["badge"]
    assert "H₁" not in rendered["footer"]
    assert "N = 6 fills" in rendered["footer"]
    # No per-fill rows exist in the payload, so no scatter dots are drawn.
    assert "pos-scatter-dot" not in rendered["chart"]
    # A probability axis never leaves 0–100%.
    axis_values = [float(v) for v in re.findall(r"(\d+\.\d)%", rendered["svgText"])]
    assert axis_values and all(0.0 <= v <= 100.0 for v in axis_values)


# --- spread cost per position (#538 fold-in) --------------------------------

def test_a_merged_pair_carries_its_assembly_price():
    # Arrange — $5.70 for 6 shares is a 95c pair.
    out = position_returns([_close(0.30)])

    # Act / Assert
    assert out["positions"][0]["spread_cost"] == pytest.approx(0.95)


def test_an_unwind_carries_no_assembly_price():
    # Arrange — an unwind never assembled a pair at a price.
    out = position_returns([_close(-0.10, method="sell")])

    # Act / Assert
    assert out["positions"][0]["spread_cost"] is None


@requires_node
def test_the_spread_cost_arm_draws_once_positions_carry_a_price():
    # Arrange — two merges at 95c. The arm used to read a field the payload
    # never sent, so it showed "metric not recorded" on every run.
    stats = build([_close(0.30), _close(0.05, cost=0.95, shares=1.0)], [], [], 85.42)

    # Act
    rendered = _render(stats, metric="spread_cost")

    # Assert
    assert "unmeasured" not in rendered["badge"]
    assert rendered["chartEmpty"] is False


def test_a_pair_at_or_over_a_dollar_lands_in_a_visible_loss_bin():
    # Arrange — the outcome the whole strategy exists to prevent. Without a bin
    # to land in it is counted in the mean and invisible in the density, which
    # is the one place an operator would look for it.
    out = pair_costs([_close(0.30, cost=5.70, shares=6.0),      # $0.95
                      _close(-0.02, cost=6.12, shares=6.0)])    # $1.02

    # Act
    overflow = [b for b in out["bins"] if b["status"] == "loss"]

    # Assert
    assert len(overflow) == 1
    assert overflow[0]["count"] == 1
    assert overflow[0]["max"] is None
    assert out["samples_count"] == 2
    assert sum(b["count"] for b in out["bins"]) == 2


def test_a_pair_at_exactly_a_dollar_is_a_loss_not_a_profit():
    # Arrange — the instrument pays exactly $1.00, so a pair assembled AT the
    # ceiling is a fill slot worth nothing.
    out = pair_costs([_close(0.0, cost=6.00, shares=6.0)])

    # Act
    overflow = [b for b in out["bins"] if b["status"] == "loss"]

    # Assert
    assert overflow[0]["count"] == 1
    assert all(b["count"] == 0 for b in out["bins"] if b["status"] == "profit")


@requires_node
def test_the_empty_state_says_unmeasured_not_zero():
    # Arrange / Act — a column of 0.00% reads as a measured flat result.
    rendered = _render(build([], [], [], 85.42))

    # Assert
    assert "unmeasured" in rendered["footer"]
    assert "0.00%" not in rendered["footer"]
    assert "unmeasured" in rendered["badge"]


def test_the_renderer_carries_no_fabricated_analytics_copy():
    # Arrange — the bell and markout footers were entirely hardcoded:
    # "82.5%", "+2.2 bps @ 300s", "48 fills", "HEALTHY", "±18.0%".
    source = _APP_JS.read_text(encoding="utf-8")

    # Act / Assert
    for invented in ("Zero Toxic Flow", "+2.2 bps @ 300s", "48 fills",
                     "<b>50.0%</b>", "±18.0%", "1,000 Paths",
                     "prob_positive_return * 100"):
        assert invented not in source, f"fabricated analytics back in app.js: {invented}"


@requires_node
def test_a_metric_no_position_carries_renders_unmeasured_not_infinity():
    """Closes with no cost basis carry no percentage.

    Deriving axis bounds from an empty set yields Infinity and formats null,
    so the chart has to recognise that its selected metric was never measured
    even though closes exist.
    """
    # Arrange — one close, dollars recorded, percent unmeasurable.
    stats = build([_close(0.30, cost=0.0)], [], [], 85.42)

    # Act
    rendered = _render(stats)

    # Assert
    assert "unmeasured" in rendered["badge"]
    assert "metric not recorded" in rendered["badge"]
    assert "Infinity" not in rendered["footer"]
    assert "NaN" not in rendered["footer"]


def test_the_renderer_carries_no_invented_metric_branches():
    # Arrange — the two remaining fabrications: a per-position spread cost, and
    # a fixed N(50, 18²) outcome distribution that described no run.
    source = _APP_JS.read_text(encoding="utf-8")

    # Act / Assert
    assert "|| 0.982" not in source
    assert "18.0 / Math.sqrt(60)" not in source

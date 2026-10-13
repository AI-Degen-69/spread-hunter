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


def _render(stats: dict) -> dict:
    out = subprocess.run(
        [shutil.which("node"), str(HARNESS), json.dumps({"statistical_analytics": stats})],
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
                     "|| 0.945", "|| 17"):
        assert invented not in source, f"invented statistic back in app.js: {invented}"


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

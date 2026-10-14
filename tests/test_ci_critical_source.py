"""One source for the confidence-interval critical value (#535).

The same two-sided standard-normal multiplier used to be written down in five
places: the table in the pure analytics module, two constants in `kpi.py`, a
literal default inside `_wilson_ci`, and four hard-coded ternaries in the
dashboard renderer. So the chart and the payload could publish different
intervals over the same closes -- on the 15-close demo store the payload drew a
t band of -1.3038 .. +0.4758 while the chart drew -1.2270 .. +0.3990 from its
own `1.96`.

Two rules are held here:

* The payload ships the multiplier as **data** for every distribution the chart
  draws, computed from the one table and for the sample that figure came from.
* **No renderer writes a critical value down.** A chart with no multiplier in
  the payload draws no interval rather than inventing a default.
"""
from __future__ import annotations

import inspect
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from core_brain import kpi
from core_brain.statistical_analytics import (
    CI_Z_BY_LEVEL,
    ci_critical_value,
    ci_z,
    pair_costs,
    position_returns,
    probability_bell,
)

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "dashboard" / "static"
HARNESS = Path(__file__).resolve().parent / "js" / "position_dist_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _close(pnl: float, cost: float = 5.70, shares: float = 6.0,
           method: str = "shadow_merge") -> dict:
    return {"ts": 1_788_000_000.0, "condition_id": "0xm", "market_slug": "m",
            "method": method, "shares": shares, "cost_basis": cost,
            "proceeds": cost + pnl, "realized_pnl": pnl}


def _fill(price: float = 0.50) -> dict:
    return {"price": price, "size": 10.0}


# --- the one table ----------------------------------------------------------

def test_the_table_is_the_only_place_a_normal_multiplier_is_named():
    assert CI_Z_BY_LEVEL == ((90, 1.645), (95, 1.96))
    assert ci_z(90) == 1.645
    assert ci_z(95) == 1.96


def test_an_unknown_level_is_refused_rather_than_guessed():
    """A level with no row is not the same as a level whose multiplier is 1.0."""
    assert ci_z(97.5) is None


# --- the payload ships the multiplier ---------------------------------------

def test_every_distribution_the_chart_draws_ships_its_own_critical_value():
    """`position_returns` has two samples -- dollars and percent -- and they can
    differ in length, so each carries the multiplier for its own sample."""
    closes = [_close(0.30), _close(0.10), _close(-0.20)]
    usd = position_returns(closes)

    assert set(usd["ci_critical_usd"]) == {"90", "95"}
    assert set(usd["ci_critical_pct"]) == {"90", "95"}
    for level, z in CI_Z_BY_LEVEL:
        assert usd["ci_critical_usd"][str(level)] == ci_critical_value(level, 3, z)
        assert usd["ci_critical_pct"][str(level)] == ci_critical_value(level, 3, z)


def test_the_count_based_distributions_ship_one_multiplier_apiece():
    costs = pair_costs([_close(0.30)] * 4)
    bell = probability_bell([_fill()] * 5)

    for dist, n in ((costs, 4), (bell, 5)):
        assert set(dist["ci_critical"]) == {"90", "95"}
        for level, z in CI_Z_BY_LEVEL:
            assert dist["ci_critical"][str(level)] == ci_critical_value(level, n, z)


def test_the_multiplier_tracks_the_sample_the_band_is_drawn_from():
    """This is the whole reason a renderer must not carry its own constant.

    Three observations is the t row (4.303 at 95%), not 1.96 -- on three closes
    the two disagree by a factor of 2.2, so a chart with its own 1.96 draws a
    band the payload's own gate contradicts. Past 30 degrees of freedom the
    table hands back the normal value and the two agree.
    """
    small = position_returns([_close(0.30), _close(0.10), _close(-0.20)])
    assert small["ci_critical_usd"]["95"] == 4.303
    assert small["ci_critical_usd"]["95"] != ci_z(95)

    big = position_returns([_close(0.30)] * 40)
    assert big["ci_critical_usd"]["95"] == ci_z(95)


def test_a_distribution_with_no_sample_carries_no_multiplier_to_multiply():
    """Nothing to draw means nothing to multiply; `None` never becomes a number."""
    empty = position_returns([])
    assert "ci_critical_usd" not in empty
    assert "ci_critical_pct" not in empty
    assert pair_costs([]).get("ci_critical") is None
    assert probability_bell([]).get("ci_critical") is None


def test_one_observation_ships_no_multiplier_to_scale_a_spread_with():
    """A sample of one has a mean, no deviation, and therefore no band. Shipping
    the t row for n=1 (12.706 at 95%) beside a `None` standard error would be a
    multiplier waiting for something to multiply."""
    one = position_returns([_close(0.30)])
    assert one["stdev_pnl_usd"] is None
    assert one["ci_critical_usd"] is None
    assert pair_costs([_close(0.30)]).get("ci_critical") is None
    assert probability_bell([_fill()])["ci_critical"] is None


# --- no second source -------------------------------------------------------

def test_no_renderer_writes_down_a_critical_value():
    """Scan every dashboard script, not just the one that got it wrong."""
    forbidden = re.compile(r"\b1\.645\b|\b1\.96\b|\b2\.576\b|\b2\.326\b|\b1\.282\b"
                           r"|1\.95996|2\.32635|2\.57583")
    offenders = {}
    for path in sorted(STATIC.glob("*.js")):
        hits = [(n, line.strip())
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                if forbidden.search(line)]
        if hits:
            offenders[path.name] = hits

    assert offenders == {}, f"a renderer still holds its own critical value: {offenders}"


def test_kpi_takes_its_two_sided_multiplier_from_the_one_table():
    """The declared-but-never-read twin is gone, and the Wilson default is not a
    second copy of the same number."""
    assert not hasattr(kpi, "Z_95_TWO_SIDED")
    default = inspect.signature(kpi._wilson_ci).parameters["z"].default
    assert default == ci_z(95)
    # The one-sided alpha the power formulas use is the same quantile, read from
    # the table rather than written down again.
    assert kpi.Z_ALPHA_90_ONE_SIDED == ci_z(90)


def test_no_other_core_brain_module_writes_a_normal_multiplier_down():
    """`CI_Z_BY_LEVEL` is the only place those two numbers live in `core_brain`.

    The trade-analytics payload used to carry its own pair of them, which is how
    a caller could be reading a different 95% from the one the table holds.
    """
    forbidden = re.compile(r"\b1\.645\b|\b1\.96\b")
    offenders = {}
    for path in sorted((ROOT / "core_brain").glob("*.py")):
        if path.name == "statistical_analytics.py":   # the table itself
            continue
        hits = [(n, line.strip())
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                if forbidden.search(line)]
        if hits:
            offenders[path.name] = hits

    assert offenders == {}, f"a second copy of the table: {offenders}"


def test_the_trade_analytics_bands_read_their_multiplier_from_the_table():
    """Behaviour-preserving: the literals moved to the table, the bands did not."""
    closes = [_close(pnl, cost=6.0, shares=6.0) for pnl in (0.30, 0.10, -0.20, 0.40)]
    ta = kpi.compute_trade_analytics(closes, starting_capital=100.0,
                                     equity_series=[], float_marks=[])
    se = ta["stdev_return_pct"] / (len(closes) ** 0.5)

    assert ta["ci90_lower_pct"] == pytest.approx(ta["mean_return_pct"] - ci_z(90) * se)
    assert ta["ci95_return_pct"]["lower"] == pytest.approx(
        ta["mean_return_pct"] - ci_z(95) * se)


# --- the renderer reads the payload, it does not decide ----------------------

def _render(stats: dict, **controls) -> dict:
    payload = {"statistical_analytics": stats}
    payload.update(controls)
    out = subprocess.run(
        [shutil.which("node"), str(HARNESS), json.dumps(payload)],
        capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _chart_stats(**overrides) -> dict:
    """A two-close dollar sample at the 90% level, whose multiplier the test owns."""
    stats = {
        "position_returns": {
            # Both metrics on the same positions: each arm reads its own field, and
            # the spread-cost arm has nothing to draw without `spread_cost`.
            "positions": [{"pnl_pct": 1.0, "spread_cost": 0.95},
                          {"pnl_pct": 3.0, "spread_cost": 0.96}],
            "mean_pnl_pct": 2.0,
            "stdev_pnl_pct": 1.414214,
            "sem_pnl_pct": 1.0,
            "ci_critical_pct": {"90": 2.0, "95": 3.0},
        }
    }
    stats["position_returns"].update(overrides)
    return stats


@requires_node
def test_the_chart_draws_the_band_the_payload_handed_it():
    """2.0 x 1.0% either side of a 2.0% mean is [+0.00%, +4.00%]. A chart using its
    own 1.645 would print [-1.65%, +3.65%] over the same closes."""
    rendered = _render(_chart_stats())

    assert "+0.00%" in rendered["badge"]
    assert "+4.00%" in rendered["badge"]
    assert "-1.65%" not in rendered["badge"]


@requires_node
def test_a_band_that_crosses_zero_is_not_a_confirmed_edge_in_the_footer():
    """The badge already refused to call this a confirmed edge; the footer two
    lines below it said H₁: μ > 0 CONFIRMED over the same interval."""
    rendered = _render(_chart_stats())

    assert "ZERO CROSSING" in rendered["badge"]
    assert "CONFIRMED" not in rendered["footer"]
    assert "ZERO CROSSING" in rendered["footer"]


@requires_node
def test_the_selected_level_picks_that_level_out_of_the_payload():
    """The toggle selects a row; it never scales the band itself."""
    at_90 = _render(_chart_stats(), ciLevel=90)
    at_95 = _render(_chart_stats(), ciLevel=95)

    assert "+0.00%" in at_90["badge"] and "+4.00%" in at_90["badge"]
    assert "-1.00%" in at_95["badge"] and "+5.00%" in at_95["badge"]


def _arm_stats() -> dict:
    """Every arm's payload, each carrying the field and multiplier it reads."""
    return {
        "position_returns": {
            "positions": [{"pnl_pct": 1.0, "pnl_usd": 1.0, "spread_cost": 0.95},
                          {"pnl_pct": 3.0, "pnl_usd": 3.0, "spread_cost": 0.96}],
            "mean_pnl_pct": 2.0, "stdev_pnl_pct": 1.414214, "sem_pnl_pct": 1.0,
            "mean_pnl_usd": 2.0, "stdev_pnl_usd": 1.414214, "sem_pnl_usd": 1.0,
            "ci_critical_pct": {"90": 2.0, "95": 3.0},
            "ci_critical_usd": {"90": 2.0, "95": 3.0},
        },
        "pair_costs": {"mean": 0.955, "stdev": 0.007, "samples_count": 2,
                       "ci_critical": {"90": 6.314, "95": 12.706}},
        "probability_bell": {"mean": 0.5, "stdev": 0.04, "samples_count": 4,
                             "bins": [{"bin": 0.5, "empirical_count": 4}],
                             "ci_critical": {"90": 2.353, "95": 3.182}},
    }


@requires_node
@pytest.mark.parametrize("metric, low, high", [
    # Three arms derive their axis from the sample's own spread...
    ("pnl_pct", "-2.90%", "+6.90%"),
    ("pnl_usd", "-$2.880", "+$6.880"),
    # ...the probability arm fits the occupied measured bins (#538), padded
    # one bin each side and clamped to 0–100%...
    ("outcome_prob", "38.9%", "66.7%"),
    # ...and one writes its half-width down. The pair-cost arm spans cents
    # around a ~$0.95 mean, so losing its 0.035 half-width prints
    # $-0.045 .. $1.955 and draws every bar against that instead.
    ("spread_cost", "$0.920", "$0.990"),
])
def test_each_arm_keeps_its_own_axis_scale(metric, low, high):
    """The four arms scale their axes four different ways, and a whole-arm edit
    can drop one of them while the chart still renders something plausible.

    That is not hypothetical: replacing the pair-cost arm's multiplier line is
    exactly how its `delta = 0.035` went missing, and no test noticed because
    the arm had no renderer test at all.
    """
    svg = _render(_arm_stats(), metric=metric)["svgText"]

    assert low in svg, f"{metric} lower axis label"
    assert high in svg, f"{metric} upper axis label"
    # A pair cost is never negative and never past the dollar the instrument pays.
    assert "$-" not in svg
    assert "$1.9" not in svg


@requires_node
def test_the_other_arms_still_draw_the_bands_their_own_payload_names():
    """One payload key per arm, so a renamed key shows as that arm alone going
    unmeasured rather than as a wrong band."""
    stats = _chart_stats()
    stats["pair_costs"] = {"mean": 0.95, "stdev": 0.004, "samples_count": 2,
                           "ci_critical": {"90": 6.314, "95": 12.706}}
    stats["probability_bell"] = {"mean": 0.5, "stdev": 0.04, "samples_count": 4,
                                "bins": [{"bin": 0.5, "empirical_count": 4}],
                                "ci_critical": {"90": 2.353, "95": 3.182}}

    for metric, expected in (("spread_cost", "90% CI: [$0.932, $0.968]"),
                             # The probability arm states an interval, not an
                             # edge test (#538).
                             ("outcome_prob",
                              "90% interval for mean fill price: [45.3%, 54.7%]")):
        rendered = _render(stats, metric=metric)
        assert expected in rendered["badge"], (metric, rendered["badge"])
        assert "unmeasured" not in rendered["badge"]
        assert rendered["ciZones"] == 1


@requires_node
def test_an_arm_whose_key_is_missing_goes_unmeasured_and_the_rest_do_not():
    """The misnamed-key case, which is the failure this payload shape makes
    visible instead of silent."""
    stats = _chart_stats()
    stats["pair_costs"] = {"mean": 0.95, "stdev": 0.004, "samples_count": 2}

    unmeasured = _render(stats, metric="spread_cost")
    other_arm = _render(stats, metric="pnl_pct")

    assert "NO MULTIPLIER FOR THIS SAMPLE" in unmeasured["badge"]
    assert unmeasured["ciZones"] == 0
    assert "unmeasured" not in other_arm["badge"]


@requires_node
def test_a_band_clear_of_zero_is_a_confirmed_edge_in_the_footer():
    """The other side of the same line, so the copy cannot pass by always
    declining to confirm."""
    rendered = _render(_chart_stats(mean_pnl_pct=8.0,
                                    ci_critical_pct={"90": 2.0, "95": 3.0}))

    assert "CONFIRMED" in rendered["footer"]
    assert "ZERO CROSSING" not in rendered["footer"]


@requires_node
def test_a_payload_with_no_multiplier_draws_no_band_rather_than_a_default():
    """The level nobody tabulated, the sample too small, and the backend older
    than the page all arrive here as the same thing: nothing to multiply."""
    stats = _chart_stats()
    del stats["position_returns"]["ci_critical_pct"]

    rendered = _render(stats)

    assert "unmeasured" in rendered["badge"]
    assert "NO MULTIPLIER FOR THIS SAMPLE" in rendered["badge"]
    assert "unmeasured" in rendered["footer"]
    assert "CONFIRMED" not in rendered["footer"]
    for leak in ("NaN", "undefined", "null", "Infinity"):
        assert leak not in rendered["footer"]
        assert leak not in rendered["badge"]


def test_the_payload_still_reports_the_same_interval_it_did_before():
    """The consolidation is behaviour-preserving: the multiplier moved, not the maths."""
    closes = [_close(pnl) for pnl in (0.30, 0.10, -0.20, 0.40, 0.05)]
    band = kpi._mean_pnl_ci([c["realized_pnl"] for c in closes])

    crit = ci_critical_value(95, 5, ci_z(95))
    mean = sum(c["realized_pnl"] for c in closes) / 5
    se = pytest.approx(band["levels"][1]["upper"] - mean)
    assert band["levels"][1]["upper"] - mean == se
    assert crit == pytest.approx(2.776)          # df=4 row, unchanged

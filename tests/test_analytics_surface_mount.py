"""The Tier 1 analytics row mounts, and no sub-view can blank it.

Issue #90 asks for a smoke test that the analytics surface mounts against
mock data. This is it, and it also pins the one failure mode a sub-nav can
introduce: a view filter that hides the go/no-go pair while showing a
projection deck.

The renderers run against the stub DOM in `tests/js/analytics_surface_harness.cjs`
and the harness reports the HTML each card produced, so the assertions are on
rendered copy rather than on a call having been made.

Journeys under test:
1. As the Owner, the go/no-go row renders from a payload without throwing.
2. As the Owner, a band that includes a loss says so, in dollars.
3. As the Owner, a run with too few closes is told no band exists, not zero.
4. As the Owner, switching analytics sub-views never hides the Tier 1 row.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "js" / "analytics_surface_harness.cjs"

_TAG = re.compile(r"<[^>]+>")


def _visible(html: str) -> str:
    """What the operator actually reads.

    Assertions about copy belong on the rendered text, not on the markup around
    it: the figure spans carry `class="kpi-value null"` for an unmeasured value,
    which is the repo's own idiom and not a leak of the word "null" to a reader.
    """
    return _TAG.sub(" ", html)

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed on this host")


def _mount(kpi: dict) -> dict:
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps({"kpi": kpi})],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _kpi(mean_pnl_ci: dict | None = None, funnel: dict | None = None) -> dict:
    return {
        "trade_analytics": {"mean_pnl_ci": mean_pnl_ci} if mean_pnl_ci is not None else {},
        "execution_funnel": funnel,
    }


def _band(level: int, lower: float, upper: float) -> dict:
    return {
        "level": level, "lower": lower, "upper": upper,
        "includes_negative": lower < 0,
        "negative_depth_usd": abs(lower) if lower < 0 else None,
    }


LOSING_CI = {
    "mean_usd": -0.34,
    "n": 7,
    "levels": [_band(90, -0.575, -0.111), _band(95, -0.620, -0.067)],
    "verdict": "negative",
}

SPANNING_CI = {
    "mean_usd": 0.08,
    "n": 12,
    "levels": [_band(90, -0.045, 0.205), _band(95, -0.070, 0.230)],
    "verdict": "spans_zero",
}

FUNNEL = {
    "stages": [
        {"key": "quoted", "label": "Quoted", "legs": 40, "markets": 3},
        {"key": "filled", "label": "Filled", "legs": 12, "markets": 2},
        {"key": "closed", "label": "Closed", "legs": 7, "markets": 1},
        {"key": "merged", "label": "Merged", "legs": 0, "markets": 0},
    ],
    "drop_off": [
        {"from": "quoted", "to": "filled", "lost": 28, "retained_pct": 30.0},
        {"from": "filled", "to": "closed", "lost": 5, "retained_pct": 58.33},
        {"from": "closed", "to": "merged", "lost": 7, "retained_pct": 0.0},
    ],
    "worst_step": {"from": "quoted", "to": "filled", "lost": 28, "retained_pct": 30.0},
    "declined": [{"reason": "PAIR_TOO_EXPENSIVE", "cycles": 41}],
}


# -- It mounts ---------------------------------------------------------------

def test_the_tier_1_row_mounts_from_a_payload_without_throwing():
    # Act
    out = _mount(_kpi(LOSING_CI, FUNNEL))

    # Assert
    assert out["errors"] == []
    assert out["pnl_ci_html"].strip()
    assert out["funnel_html"].strip()


def test_an_empty_payload_still_mounts():
    # Arrange -- a dashboard opened before any run has to render, not blank.
    out = _mount({})

    # Assert
    assert out["errors"] == []
    assert out["pnl_ci_html"].strip()


# -- What the cards actually say ---------------------------------------------

def test_a_band_below_zero_reads_as_a_loss_not_as_a_number():
    # Act
    out = _mount(_kpi(LOSING_CI, FUNNEL))

    # Assert -- the dollar figures and the verdict are both on screen.
    assert "-$0.34" in out["pnl_ci_html"]
    assert "90%" in out["pnl_ci_html"] and "95%" in out["pnl_ci_html"]
    assert "BELOW ZERO" in out["pnl_ci_html"]


def test_a_band_that_spans_zero_names_how_deep_the_loss_goes():
    # Act
    out = _mount(_kpi(SPANNING_CI, FUNNEL))

    # Assert -- "includes a loss" without the depth is the softening this
    # readout exists to prevent.
    assert "INCLUDES A LOSS" in out["pnl_ci_html"]
    assert "-$0.070" in out["pnl_ci_html"]


def test_too_few_closes_reports_no_band_rather_than_zero():
    # Arrange -- one close, so no interval exists.
    ci = {"mean_usd": 0.25, "n": 1, "levels": [], "verdict": None}

    # Act
    out = _mount(_kpi(ci, FUNNEL))

    # Assert
    assert "NO BAND YET" in out["pnl_ci_html"]
    assert "1 CLOSE" in out["pnl_ci_html"]


def test_the_funnel_names_its_worst_step_and_the_legs_it_lost():
    # Act
    out = _mount(_kpi(LOSING_CI, FUNNEL))

    # Assert
    html = out["funnel_html"]
    assert "40 legs" in html and "3 markets" in html
    assert "30.0% carried on" in html
    assert "28 lost" in html
    assert "PAIR_TOO_EXPENSIVE" in html


def test_a_run_with_no_execution_telemetry_says_so():
    # Act
    out = _mount(_kpi(LOSING_CI, None))

    # Assert
    assert "No execution telemetry" in out["funnel_html"]


# -- No sub-view can hide the decision row -----------------------------------

@pytest.mark.parametrize("view", ["all", "distributions", "monte-carlo",
                                  "markout", "simulator", "markets"])
def test_no_analytics_sub_view_hides_the_go_no_go_row(view):
    # Act
    out = _mount(_kpi(LOSING_CI, FUNNEL))

    # Assert -- the filter may hide decks; it may never hide Tier 1. The
    # harness swallows a throwing filter, so a stored display of '' would
    # otherwise pass this test on a filter that never ran at all.
    assert not [e for e in out["errors"] if e.startswith("filter ")]
    assert out["tier1_visibility"][view] != "none"


# -- Sample size sufficiency rendering (#443) --------------------------------

def test_sample_sufficiency_renders_three_levels():
    payload = {
        "current_n": 4,
        "std_dev_usd": 0.11547,
        "target_margin_usd": 0.02,
        "levels": [
            {"confidence_pct": 95, "z": 1.95996, "required_n": 129, "remaining_n": 125, "progress_pct": 3},
            {"confidence_pct": 98, "z": 2.32635, "required_n": 181, "remaining_n": 177, "progress_pct": 2},
            {"confidence_pct": 99, "z": 2.57583, "required_n": 222, "remaining_n": 218, "progress_pct": 2},
        ],
    }
    kpi = {"trade_analytics": {"sample_size_sufficiency": payload}}
    out = _mount(kpi)
    assert not out["errors"]
    html = out["sample_sufficiency_html"]
    assert "95%" in html and "98%" in html and "99%" in html
    assert "129" in html and "181" in html and "222" in html
    assert "125" in html and "177" in html and "218" in html
    assert "dist-progress-bar" in html
    assert "dist-progress-fill" in html


def test_sample_sufficiency_n_zero_has_no_undefined():
    payload = {
        "current_n": 0,
        "std_dev_usd": None,
        "target_margin_usd": 0.02,
        "levels": [
            {"confidence_pct": 95, "z": 1.95996, "required_n": None, "remaining_n": None, "progress_pct": None},
            {"confidence_pct": 98, "z": 2.32635, "required_n": None, "remaining_n": None, "progress_pct": None},
            {"confidence_pct": 99, "z": 2.57583, "required_n": None, "remaining_n": None, "progress_pct": None},
        ],
    }
    kpi = {"trade_analytics": {"sample_size_sufficiency": payload}}
    out = _mount(kpi)
    assert not out["errors"]
    html = out["sample_sufficiency_html"]
    assert "unmeasured" in html
    assert "undefined" not in html
    assert "NaN" not in html
    assert "At least two closed trades are needed" in html


def test_sample_sufficiency_renders_segmented_statuses():
    payload = {
        "current_n": 4,
        "std_dev_usd": 0.11547,
        "target_margin_usd": 0.02,
        "effect_size_d": 0.20,
        "statuses": {
            "pnl_expectancy": {
                "label": "PnL Expectancy",
                "type": "continuous",
                "base": "closes",
                "current_n": 4,
                "effect_size_d": 0.20,
                "levels": [
                    {"confidence_pct": 95, "z": 1.95996, "required_n": 97, "remaining_n": 93, "progress_pct": 4},
                ],
            },
            "stop_loss_rate": {
                "label": "Stop Loss Rate",
                "type": "proportion",
                "base": "closes",
                "current_n": 4,
                "target_margin": 0.05,
                "levels": [
                    {"confidence_pct": 95, "z": 1.95996, "required_n": 385, "remaining_n": 381, "progress_pct": 1},
                ],
            },
            "merge_rate": {
                "label": "Merge Rate",
                "type": "proportion",
                "base": "closes",
                "current_n": 4,
                "target_margin": 0.05,
                "levels": [
                    {"confidence_pct": 95, "z": 1.95996, "required_n": 385, "remaining_n": 381, "progress_pct": 1},
                ],
            },
            "fill_rate": {
                "label": "Fill Rate",
                "type": "proportion",
                "base": "orders",
                "current_n": 15,
                "target_margin": 0.05,
                "levels": [
                    {"confidence_pct": 95, "z": 1.95996, "required_n": 385, "remaining_n": 370, "progress_pct": 4},
                ],
            },
        },
        "levels": [
            {"confidence_pct": 95, "z": 1.95996, "required_n": 97, "remaining_n": 93, "progress_pct": 4},
        ],
    }
    kpi = {"trade_analytics": {"sample_size_sufficiency": payload}}
    out = _mount(kpi)
    assert not out["errors"]
    html = out["sample_sufficiency_html"]
    assert "PnL Expectancy" in html
    assert "Stop Loss Rate" in html
    assert "Merge Rate" in html
    assert "Fill Rate" in html
    assert "Cohen&#39;s d = 0.2" in html or "Cohen's d = 0.2" in html
    assert "Margin = 5%" in html
    assert "(closes)" in html
    assert "(orders)" in html
    assert "97" in html
    assert "385" in html
    assert "undefined" not in html
    assert "NaN" not in html


# -- Merge edge vs rescue bleed (#513) ---------------------------------------

# The mixed case from `tests/test_fill_path_analytics.py`: three merges at
# +$0.30, one rescue at -$0.60, one settlement, one sync row.
FILL_PATH_EDGE = {
    "merge_count": 3, "merge_pnl_usd": 0.90, "merge_expectancy_usd": 0.30,
    "rescue_count": 1, "rescue_pnl_usd": -0.60, "rescue_expectancy_usd": -0.60,
    "rescue_rate": 0.25, "net_expectancy_usd": 0.075,
    "break_even_rescue_rate": 0.333333, "break_even_status": "measured",
    "other_count": 1, "other_pnl_usd": 1.0,
    "excluded_count": 1, "skipped_count": 0, "units": "usd_per_close",
}


def _stats(edge) -> dict:
    """A payload carrying the edge, or one from before the key existed."""
    return {"statistical_analytics": {} if edge is None else {"fill_path_edge": edge}}


def test_the_fill_path_readout_shows_both_paths_and_the_threshold():
    # Act
    out = _mount(_stats(FILL_PATH_EDGE))

    # Assert -- both expectancies per close, the measured rescue rate, and the
    # rate at which the two paths cancel out.
    assert out["errors"] == []
    text = _visible(out["fill_path_edge_html"])
    assert "per close" in text
    assert "0.30" in text       # merge expectancy, +$0.30
    assert "0.60" in text       # rescue expectancy, -$0.60
    assert "25.0%" in text      # rescue rate, 1 of 4 completed closes
    assert "33.3%" in text      # break-even rescue rate
    assert "1 of 4 completed closes" in text


def test_the_fill_path_readout_counts_closes_in_the_singular():
    # Arrange -- one close on a path reads as "1 close", not "1 closes".
    edge = dict(FILL_PATH_EDGE, merge_count=1, rescue_count=2)

    # Act
    out = _mount(_stats(edge))

    # Assert
    assert out["errors"] == []
    text = _visible(out["fill_path_edge_html"])
    assert "per close · 1 close" in text
    assert "per close · 2 closes" in text
    assert "1 closes" not in text


def test_the_fill_path_readout_keeps_sub_cent_expectancies_distinguishable():
    # Arrange -- a real per-close edge lives below one cent: the two-path edge
    # on the real shadow store is about -$0.01/close. At two decimals a measured
    # +$0.004 edge would print as `+$0.00`, an edge reading as no edge.
    edge = dict(FILL_PATH_EDGE, merge_expectancy_usd=0.004,
                rescue_expectancy_usd=-0.002, net_expectancy_usd=0.001)

    # Act
    out = _mount(_stats(edge))

    # Assert
    assert out["errors"] == []
    text = _visible(out["fill_path_edge_html"])
    assert "+$0.004" in text
    assert "-$0.002" in text
    assert "+$0.001" in text


def test_the_fill_path_readout_says_unmeasured_rather_than_zero():
    # Arrange -- an empty run, and a payload served before the key existed.
    for edge in (None, {}):
        # Act
        out = _mount(_stats(edge))

        # Assert -- a value nobody could compute reads as unmeasured, and the
        # operator never sees a raw `undefined` or `NaN` on the card.
        assert out["errors"] == []
        text = _visible(out["fill_path_edge_html"])
        assert "unmeasured" in text
        for leak in ("NaN", "undefined", "null"):
            assert leak not in text


def test_the_fill_path_readout_names_why_a_threshold_is_missing():
    # Arrange -- merges that lose cannot be balanced by any rescue rate, so the
    # card says so in words instead of printing a number.
    edge = {**FILL_PATH_EDGE, "merge_expectancy_usd": -0.10,
            "break_even_rescue_rate": None,
            "break_even_status": "merge_not_profitable"}

    # Act
    out = _mount(_stats(edge))

    # Assert
    assert out["errors"] == []
    text = _visible(out["fill_path_edge_html"])
    assert "no rescue rate breaks even while merges lose" in text
    assert "33.3%" not in text


# -- Rescue friction: what a bailout cost to execute (#514) -------------------

# The five rescue closes from `tests/test_rescue_friction_analytics.py`, averaged:
# a 15.8c mean SLED that is mostly one 68c stale-mark exit, 17.75c of latency drag,
# 0.4c of execution drag, 1.6% of the booked loss in fees, and 0.41x the planned risk.
RESCUE_FRICTION = {
    "rescue_count": 5, "measured_count": 5,
    "recorded_reference_count": 4, "assumed_reference_count": 1,
    "fee_measured_count": 4, "non_rescue_count": 3,
    "mean_sled_cents": 15.8, "mean_latency_drag_cents": 17.75,
    "mean_execution_drag_cents": 0.4, "mean_tcdr": 0.015625,
    "mean_overshoot_ratio": 0.40625,
    "bands": {"sled": "bad", "latency": "good", "execution": "good",
              "tcdr": "average", "overshoot": "average"},
    "units": "cents_per_share",
}


def _friction(payload) -> dict:
    """A payload carrying the friction summary, or one from before it existed."""
    return {"statistical_analytics": {} if payload is None else {"rescue_friction": payload}}


def test_the_rescue_friction_readout_shows_each_metric_in_its_own_unit():
    # Act
    out = _mount(_friction(RESCUE_FRICTION))

    # Assert -- cents for the three drags, a share of the loss for the fee ratio, a
    # multiple of the planned risk for the overshoot.
    assert out["errors"] == []
    text = _visible(out["rescue_friction_html"])
    assert "SLED (¢)" in text
    assert "15.80" in text       # mean sled, in cents
    assert "17.75" in text       # latency drag
    assert "0.40" in text        # execution drag
    assert "1.6%" in text        # fee share of the booked loss
    assert "0.41x" in text       # drag against the planned risk


def test_the_readout_discloses_where_the_numbers_came_from():
    # Arrange -- the operator has to know how much of the drag rests on the named
    # offset rather than a recorded bid, and that non-rescue closes stayed out.
    # Act
    out = _mount(_friction(RESCUE_FRICTION))

    # Assert
    text = _visible(out["rescue_friction_html"])
    assert "Measured 5 of 5 rescue closes" in text
    assert "triggers recorded 4" in text
    assert "assumed 0.04 offset 1" in text
    assert "fees measured 4" in text
    assert "3 other closes left out of every mean" in text


def test_each_metric_carries_the_colour_the_payload_computed():
    # Arrange -- the band arrives as data; the renderer only names a colour for it.
    # `live` is this theme's green chip, `warn` its amber, `bad` its red.
    # Act
    out = _mount(_friction(RESCUE_FRICTION))

    # Assert
    html = out["rescue_friction_html"]
    assert "analytics-chip bad" in html        # sled: 15.8c mean
    assert "analytics-chip live" in html       # latency + execution
    assert "analytics-chip warn" in html       # tcdr + overshoot
    text = _visible(html)
    assert "BAD" in text and "GOOD" in text and "AVERAGE" in text


def test_the_colour_follows_the_value_not_a_static_label():
    # Arrange -- the same card with a stop that paid nothing: the sled band is good.
    payload = {**RESCUE_FRICTION, "mean_sled_cents": -0.5,
               "bands": {**RESCUE_FRICTION["bands"], "sled": "good"}}

    # Act
    out = _mount(_friction(payload))

    # Assert
    html = out["rescue_friction_html"]
    assert "analytics-chip bad" not in html
    text = _visible(html)
    assert "BAD" not in text
    assert "-0.50" in text      # price improvement still shown, in cents


def test_an_unmeasured_run_says_unmeasured_not_zero():
    # Arrange -- a store whose rescue closes carry none of the three prices.
    # Act
    out = _mount(_friction({
        "rescue_count": 2, "measured_count": 0,
        "recorded_reference_count": 0, "assumed_reference_count": 0,
        "fee_measured_count": 0, "non_rescue_count": 1,
        "mean_sled_cents": None, "mean_latency_drag_cents": None,
        "mean_execution_drag_cents": None, "mean_tcdr": None,
        "mean_overshoot_ratio": None,
        "bands": {name: "unmeasured" for name in
                  ("sled", "latency", "execution", "tcdr", "overshoot")},
        "units": "cents_per_share",
    }))

    # Assert -- every tile says unmeasured; `0.00` would read as a measured flat cost.
    assert out["errors"] == []
    text = _visible(out["rescue_friction_html"])
    assert text.count("unmeasured") >= 5
    assert "0.00" not in text
    # The population is still disclosed: two closes came through, none measurable.
    assert "Measured 0 of 2 rescue closes" in text
    for leak in ("NaN", "undefined", "null"):
        assert leak not in text


def test_a_store_from_before_this_section_reads_as_unmeasured():
    # Arrange / Act -- a payload with no `rescue_friction` key at all, which is what
    # every store written before this change looks like.
    for payload in (None, {}):
        out = _mount(_friction(payload))

        # Assert
        assert out["errors"] == []
        text = _visible(out["rescue_friction_html"])
        assert "unmeasured" in text
        for leak in ("NaN", "undefined", "null"):
            assert leak not in text



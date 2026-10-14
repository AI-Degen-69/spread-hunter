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


# The same edge, now carrying the traffic-light bands the server computes.
FILL_PATH_EDGE_BANDED = dict(FILL_PATH_EDGE, bands={
    "merge_expectancy": "good", "rescue_expectancy": "bad",
    "rescue_rate": "good", "break_even_rescue_rate": "average",
    "net_expectancy": "good",
})


def test_the_fill_path_readout_colours_every_metric_with_its_band():
    # Arrange / Act
    out = _mount(_stats(FILL_PATH_EDGE_BANDED))

    # Assert -- each tile carries the band as a chip, in the same classes the
    # friction card uses, so one colour vocabulary covers both readouts.
    assert out["errors"] == []
    html = out["fill_path_edge_html"]
    assert 'analytics-chip live' in html   # good
    assert 'analytics-chip warn' in html   # average
    assert 'analytics-chip bad' in html    # bad
    for band in ("GOOD", "AVERAGE", "BAD"):
        assert band in _visible(html)


def test_the_fill_path_readout_renders_no_band_without_a_bands_key():
    # Arrange / Act -- a payload served before the key existed (or a hand-built
    # one) must still render, and must not invent chips for it.
    out = _mount(_stats(FILL_PATH_EDGE))

    # Assert
    assert out["errors"] == []
    assert "analytics-chip" not in out["fill_path_edge_html"]


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
    "bands": {"sled": "bad", "latency": "bad", "execution": "good",
              "tcdr": "average", "overshoot": "average"},
    "assumed_stop_offset": 0.03,
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
    # The offset is the payload's own number, not a copy the card carries: this store
    # assumes 0.03, so the card has to say 0.03.
    assert "assumed 0.03 offset 1" in text
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
    # Arrange -- the same card with a stop that paid nothing: sled and latency good.
    payload = {**RESCUE_FRICTION, "mean_sled_cents": -0.5,
               "mean_latency_drag_cents": -1.0,
               "bands": {**RESCUE_FRICTION["bands"], "sled": "good",
                         "latency": "good"}}

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


# -- Queue realism: the tape behind a fill (#515) ----------------------------

# A store where both readings are measurable: four cleared fills, three of them carrying a
# print 2.5c below our price (median), and waits of 5s / 10s / 15s.
QUEUE_REALISM = {
    "cleared_fill_count": 4,
    "trade_through_depth": {"count": 3, "missing": 1, "not_trade_through": 0,
                            "median_cents": 2.5, "p75_cents": 4.25, "band": "average"},
    "fill_latency": {"count": 3, "excluded": 1, "median_sec": 10.0, "p90_sec": 14.0},
}


def _queue(payload) -> dict:
    """A payload carrying the queue-realism section, or one from before it existed."""
    return {"statistical_analytics": {} if payload is None else {"queue_realism": payload}}


def test_the_queue_realism_readout_shows_cents_for_depth_and_seconds_for_the_wait():
    # Act
    out = _mount(_queue(QUEUE_REALISM))

    # Assert -- the depth in cents, the wait in seconds with its unit attached: a wait
    # without a unit is a number nobody can read against anything.
    assert out["errors"] == []
    text = _visible(out["queue_realism_html"])
    assert "Trade-Through Depth (¢)" in text
    assert "2.50" in text        # median depth, in cents
    assert "4.25" in text        # p75
    assert "10.0s" in text       # median wait
    assert "14.0s" in text       # p90 wait


def test_the_depth_carries_a_band_and_the_wait_carries_none():
    # Arrange -- the band arrives as data; the renderer only names a colour for it. The
    # wait is deliberately bandless: no market reference exists for it.
    # Act
    out = _mount(_queue(QUEUE_REALISM))

    # Assert -- exactly one chip in the card, and it is the depth's.
    html = out["queue_realism_html"]
    assert html.count("analytics-chip") == 1
    assert "analytics-chip warn" in html      # average is this theme's amber
    assert "AVERAGE" in _visible(html)


def test_the_depth_colour_follows_the_value_not_a_static_label():
    # Arrange -- the same card on a store where the tape crossed us within the cent.
    payload = {**QUEUE_REALISM,
               "trade_through_depth": {**QUEUE_REALISM["trade_through_depth"],
                                       "median_cents": 0.4, "band": "good"}}

    # Act
    out = _mount(_queue(payload))

    # Assert
    html = out["queue_realism_html"]
    assert "analytics-chip live" in html
    assert "analytics-chip warn" not in html
    assert "0.40" in _visible(html)


def test_a_store_with_no_recorded_print_says_unmeasured_not_a_zero_depth():
    # Arrange -- the state every store is in today: nothing records the print that cleared
    # our level, so the depth has no rows while the waits are real numbers.
    # Act
    out = _mount(_queue({
        "cleared_fill_count": 4,
        "trade_through_depth": {"count": 0, "missing": 4, "not_trade_through": 0,
                                "median_cents": None, "p75_cents": None,
                                "band": "unmeasured"},
        "fill_latency": {"count": 4, "excluded": 0, "median_sec": 12.5, "p90_sec": 18.0},
    }))

    # Assert -- the depth reads unmeasured (never `0.00`, which would claim the tape sat on
    # our price), the wait still shows its numbers, and the counts say why.
    assert out["errors"] == []
    text = _visible(out["queue_realism_html"])
    assert "UNMEASURED" in text
    assert "12.5s" in text and "18.0s" in text
    assert "0.00" not in text
    assert "prints not recorded 4" in text
    for leak in ("NaN", "undefined", "null"):
        assert leak not in text


def test_the_readout_discloses_the_population_behind_each_figure():
    # Act
    out = _mount(_queue(QUEUE_REALISM))

    # Assert -- the base population, and what each metric could not measure.
    text = _visible(out["queue_realism_html"])
    assert "4 cleared fills" in text
    assert "depth measured 3" in text
    assert "prints not recorded 1" in text
    assert "waits measured 3" in text
    assert "timestamps unusable 1" in text


def test_a_store_from_before_this_section_reads_as_unmeasured():
    # Arrange / Act -- a payload with no `queue_realism` key at all, which is what every
    # store written before this change, and every run with no quotes, looks like.
    for payload in (None, {}):
        out = _mount(_queue(payload))

        # Assert
        assert out["errors"] == []
        text = _visible(out["queue_realism_html"])
        assert "unmeasured" in text
        assert "0 cleared fills" in text
        for leak in ("NaN", "undefined", "null"):
            assert leak not in text


# -- Robustness gates: markout, MAE, CI and SQN (#516) -----------------------

# A store where all four readings have an answer: the tape took 2.5c off the mid by the five
# minute mark, the worst position ran 3.25c against us (p90 8.6c), and twelve closes leave the
# mean's 95% band straddling zero while SQN reads a stable edge.
ROBUSTNESS = {
    "units": "cents_per_share",
    "markout": {"intervals": [
        {"horizon": "5m", "markout_cents": -2.5, "samples": 12, "band": "bad",
         "meaning": "the tape moved a full cent or more against us after the fill"},
        {"horizon": "15m", "markout_cents": -0.4, "samples": 11, "band": "average",
         "meaning": "mid drifted against us by less than a tick"},
        {"horizon": "1h", "markout_cents": 0.0, "samples": 9, "band": "good",
         "meaning": "mid held or moved in our favour after the fill"},
        {"horizon": "6h", "markout_cents": 0.6, "samples": 5, "band": "good",
         "meaning": "mid held or moved in our favour after the fill"},
    ]},
    "mae": {"measured": 7, "open": 3, "exit_fill": 4, "no_marks_in_window": 2,
            "not_recorded": 1, "median_cents": 3.25, "p90_cents": 8.6,
            "units": "cents_per_share"},
    "mean_pnl_ci": {"level": 95, "mean_cents": 1.2, "lower_cents": -0.45,
                    "upper_cents": 2.85, "n": 12, "band": "average",
                    "meaning": "zero is inside the band -- suggestive, not proven"},
    "sqn": {"sqn": 2.85, "band": "good", "meaning": "stable edge -- signal dominates noise",
            "n": 12, "mean_usd": 0.012, "stdev_usd": 0.0146},
}


def _robust(payload) -> dict:
    """A payload carrying the robustness section, or one from before it existed."""
    return {"statistical_analytics": {} if payload is None else {"robustness": payload}}


def test_the_robustness_card_shows_all_four_readings_in_cents():
    # Act
    out = _mount(_robust(ROBUSTNESS))

    # Assert -- every horizon in cents, the excursion as a median and a p90, the interval as its
    # two bounds, and the score with its own number.
    assert out["errors"] == []
    text = _visible(out["robustness_html"])
    assert "Markout 5m (\u00a2)" in text
    assert "-2.50" in text          # five-minute median, in cents
    assert "-0.40" in text
    assert "0.00" in text
    assert "0.60" in text
    assert "MAE median (\u00a2)" in text
    assert "3.25" in text
    assert "8.60" in text            # p90
    assert "95% CI (\u00a2)" in text
    assert "-0.45" in text and "2.85" in text
    assert "SQN" in text
    assert "2.85" in text


def test_the_banded_tiles_carry_a_chip_and_the_excursion_carries_none():
    """Four markout horizons, the interval and the score: six chips, and neither MAE tile.

    The excursion is descriptive on purpose -- "3.25c of excursion" has no verdict until it can
    be compared against the stop distance that was meant to cap it, so a chip there would be a
    confident guess.
    """
    # Act
    out = _mount(_robust(ROBUSTNESS))

    # Assert
    html = out["robustness_html"]
    assert html.count("analytics-chip") == 6
    assert html.count("analytics-chip bad") == 1        # the 5m markout
    assert html.count("analytics-chip warn") == 2       # the 15m markout and the interval
    assert html.count("analytics-chip live") == 3       # 1h, 6h and SQN
    text = _visible(html)
    assert "STABLE EDGE -- SIGNAL DOMINATES NOISE" in text.upper()


# -- Capital risk: drawdown recovery, skewness and Sortino (#517) -----------

# The demo store's own numbers: fifteen closes of mostly small wins with a few large losses, and an
# account-value curve with three drawdowns that all came back, the worst of them 14.60 deep and
# eleven points underwater at +11.96% of its peak.
CAPITAL = {
    "units": "percent_per_close_and_curve_points",
    "pnl_skewness": {"pnl_skewness": -1.504061, "band": "bad",
                     "meaning": "downside tails -- the catastrophic-loss shape", "n": 15},
    "sortino_ratio": {"sortino_ratio": -0.23345, "risk_free_pct_per_close": 0.0,
                      "band": "bad", "meaning": "the downside pain outweighs the returns",
                      "n": 15},
    "drawdown_recovery": {
        "units": "curve_points_and_seconds", "episodes": 3, "open": 0, "skipped": 0,
        "deepest_usd": 14.6, "deepest_pct": 11.957412, "deepest_recovered": True,
        "drawdown_recovery_trades": {"median": 4.0, "p90": 7.2, "n": 3},
        "drawdown_recovery_sec": {"median": 4800.0, "p90": 8640.0, "n": 3},
    },
}


def _capital(payload) -> dict:
    """A payload carrying the capital-risk section, or one from before it existed."""
    return {"statistical_analytics": {} if payload is None else {"capital_risk": payload}}


def test_the_capital_risk_card_shows_all_three_readings():
    # Act
    out = _mount(_capital(CAPITAL))

    # Assert -- the depth in dollars and as a share of its peak, the wait in marks and in time, and
    # the two banded readings with their own figures.
    assert out["errors"] == []
    text = _visible(out["capital_risk_html"])
    assert "Deepest DD ($)" in text
    assert "14.60" in text
    assert "11.96" in text            # percent of the peak it fell from
    assert "regained" in text
    assert "Recovery median (marks)" in text
    assert "4" in text
    assert "1h 20m" in text           # 4800 seconds, in the page's own duration format
    assert "Recovery p90 (marks)" in text
    assert "7.2" in text
    assert "2h 24m" in text
    assert "PnL skewness" in text
    assert "-1.50" in text
    assert "Sortino" in text
    assert "-0.23" in text
    assert "risk-free 0.00% per close" in text


def test_only_the_two_banded_tiles_carry_a_chip():
    """Recovery is descriptive: a chip on a guess is a confident lie."""
    # Act
    out = _mount(_capital(CAPITAL))

    # Assert
    html = out["capital_risk_html"]
    assert html.count("analytics-chip") == 2
    assert html.count("analytics-chip bad") == 2
    assert "DOWNSIDE TAILS" in _visible(html).upper()


def test_the_colour_of_the_capital_chips_follows_the_payload_band():
    # Arrange -- the same readings, where the run's tails are on the upside and the edge pays.
    payload = {
        **CAPITAL,
        "pnl_skewness": {**CAPITAL["pnl_skewness"], "pnl_skewness": 0.4, "band": "good"},
        "sortino_ratio": {**CAPITAL["sortino_ratio"], "sortino_ratio": 2.4, "band": "good"},
    }

    # Act
    out = _mount(_capital(payload))

    # Assert -- green, and no red anywhere: the renderer holds no threshold.
    html = out["capital_risk_html"]
    assert html.count("analytics-chip live") == 2
    assert "analytics-chip bad" not in html


def test_a_reading_the_store_could_not_make_says_unmeasured_not_zero():
    # Arrange -- one close and no curve: every figure is missing rather than zero.
    payload = {
        "units": "percent_per_close_and_curve_points",
        "pnl_skewness": {"pnl_skewness": None, "band": "unmeasured",
                         "meaning": "fewer than three closes, or no spread to divide by", "n": 1},
        "sortino_ratio": {"sortino_ratio": None, "risk_free_pct_per_close": 0.0,
                          "band": "unmeasured", "meaning": "fewer than two closes", "n": 1},
        "drawdown_recovery": {
            "units": "curve_points_and_seconds", "episodes": 0, "open": 0, "skipped": 0,
            "deepest_usd": None, "deepest_pct": None, "deepest_recovered": None,
            "drawdown_recovery_trades": {"median": None, "p90": None, "n": 0},
        },
    }

    # Act
    out = _mount(_capital(payload))

    # Assert -- unmeasured names itself without a colour, and nothing reads as a measured zero or
    # leaks a placeholder to the reader.
    assert out["errors"] == []
    html = out["capital_risk_html"]
    text = _visible(html)
    assert "UNMEASURED" in text.upper()
    assert "analytics-chip live" not in html
    assert "analytics-chip warn" not in html
    assert "analytics-chip bad" not in html
    assert "$0.00" not in text          # a depth nobody measured is not a depth of zero
    for leak in ("NaN", "undefined", "null"):
        assert leak not in text


def test_a_store_from_before_this_section_reads_as_unmeasured_without_chips():
    # Arrange / Act -- a payload with no `capital_risk` key at all, which is what every store
    # written before this change looks like.
    out = _mount(_capital(None))

    # Assert
    assert out["errors"] == []
    html = out["capital_risk_html"]
    assert "analytics-chip" not in html
    text = _visible(html)
    assert "unmeasured" in text
    assert "Capital risk unmeasured" in text
    # A count nobody took is not a count of zero. `0 / 0` regained and `every curve point usable`
    # are claims about capital, and a store that never carried this section never measured either.
    assert "0 / 0" not in text
    assert "every curve point usable" not in text
    assert "0 closes" not in text
    for leak in ("NaN", "undefined", "null"):
        assert leak not in text


def test_the_colour_follows_the_payload_band_not_a_copy_of_it():
    # Arrange -- the same card where the tape went our way within the minute.
    payload = {
        **ROBUSTNESS,
        "markout": {"intervals": [{**ROBUSTNESS["markout"]["intervals"][0],
                                    "markout_cents": 0.4, "band": "good"}]},
    }

    # Act
    out = _mount(_robust(payload))

    # Assert -- one chip, green, and no red anywhere: the renderer holds no threshold, so a
    # payload that changes its mind changes the screen.
    html = out["robustness_html"]
    assert html.count("analytics-chip") == 3
    assert "analytics-chip live" in html
    assert "analytics-chip bad" not in html


def test_an_unmeasured_band_reads_unmeasured_without_a_colour():
    # Arrange -- one close: the interval has no band, so the score has none either.
    payload = {
        "units": "cents_per_share",
        "markout": {"intervals": []},
        "mae": {"measured": 0, "open": 0, "exit_fill": 0, "no_marks_in_window": 0,
                "not_recorded": 0, "median_cents": None, "p90_cents": None,
                "units": "cents_per_share"},
        "mean_pnl_ci": {"level": 95, "mean_cents": 2.0, "lower_cents": None,
                        "upper_cents": None, "n": 1, "band": "unmeasured",
                        "meaning": "fewer than two closes -- no band exists"},
        "sqn": {"sqn": None, "band": "unmeasured",
                "meaning": "fewer than two closes, or no spread to measure",
                "n": 1, "mean_usd": 0.02, "stdev_usd": None},
    }

    # Act
    out = _mount(_robust(payload))

    # Assert -- unmeasured is a reading, so it names itself without claiming a colour, and the
    # excursion that measured nothing shows no `0.00` that would read as a perfect entry.
    assert out["errors"] == []
    html = out["robustness_html"]
    text = _visible(html)
    assert "UNMEASURED" in text
    assert "analytics-chip live" not in html
    assert "analytics-chip warn" not in html
    assert "analytics-chip bad" not in html
    assert "3.25" not in text


def test_the_card_discloses_what_it_could_not_measure():
    # Act
    out = _mount(_robust(ROBUSTNESS))

    # Assert -- the population behind each figure, in words an operator reads off the screen.
    text = _visible(out["robustness_html"])
    assert "7 positions measured" in text
    assert "open 3" in text
    assert "no mark in window 2" in text
    assert "fill unrecorded 1" in text
    assert "12 closes" in text


def test_a_payload_from_before_this_section_renders_no_chips_at_all():
    """Back-compat is tested, not assumed: an older payload gets words, never a verdict."""
    for payload in (None, {}):
        out = _mount(_robust(payload))

        assert out["errors"] == []
        html = out["robustness_html"]
        text = _visible(html)
        assert "analytics-chip" not in html
        assert "unmeasured" in text
        for leak in ("NaN", "undefined", "null"):
            assert leak not in text



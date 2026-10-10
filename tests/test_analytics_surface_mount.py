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
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "js" / "analytics_surface_harness.cjs"

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



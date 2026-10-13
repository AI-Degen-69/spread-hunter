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

"""Capital risk: how deep and how long the curve was underwater, and where the tail risk sits (#517).

Three readings, none of them a chart. The drawdown half asks how far the capital curve fell and how
long it took to regain the old peak -- descriptive on purpose, because "recovered in four marks is
good" has no meaning without knowing how fast this strategy normally compounds. The other half asks
whether the swings come from upside surges or from downside tails (the third standardized moment),
and whether the return is worth the downside pain once the risk-free rate is named.

This file holds the pure functions, and it follows the rule `tests/test_statistical_analytics.py`,
`tests/test_queue_realism_analytics.py` and `tests/test_markout_robustness_analytics.py` all hold:
**a measurement whose inputs were never recorded is `None`, never `0`.** A drawdown that never dropped
is `0.0` because the curve measured that; an empty curve is `None` because it measured nothing, and a
band nobody could judge reads `unmeasured`, never `bad`.

The existing statistics are reused, never rebuilt: the 95% band on mean close PnL and the system
quality number live once in the module (#516), the Sortino denominator is the one `kpi` already uses,
and `kpi.compute_trade_analytics` is the oracle these functions are checked against.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from core_brain import kpi
from core_brain import statistical_analytics as sa
from core_brain.statistical_analytics import (
    RISK_FREE_PCT_PER_CLOSE,
    SKEWNESS_BAND_WORDS,
    SORTINO_BAND_WORDS,
    drawdown_episodes,
    drawdown_recovery,
    pnl_skewness,
    skewness_band,
    sortino,
    sortino_band,
    sortino_ratio,
)


def _curve(*pairs) -> list[dict]:
    """A caller-supplied equity curve, in the order given.

    A bare number is a point with no timestamp; a `(value, ts)` pair carries one. Both shapes are what
    a caller holds: the registry's account marks carry `ts`, a synthetic test curve may not.
    """
    points = []
    for pair in pairs:
        points.append({"v": pair[0], "ts": pair[1]} if isinstance(pair, tuple) else {"v": pair})
    return points


# A curve that rises, gives back 200 of it, and regains the old peak: the shape the ticket names.
RECOVERY = _curve((1100.0, 100.0), (900.0, 200.0), (950.0, 300.0), (1100.0, 400.0), (1200.0, 500.0))


def _only(out: dict) -> dict:
    durations = out["drawdown_recovery_trades"]
    assert durations["n"] == 1, out
    return durations


# ---------------------------------------------------------------------------
# The drawdown half
# ---------------------------------------------------------------------------

def test_the_deepest_drawdown_and_how_long_it_took_to_regain_are_both_measured():
    """200 dollars of peak-to-trough, regained three points and 300 seconds later."""
    out = drawdown_recovery(RECOVERY, starting_value=1000.0)

    assert out["units"] == "curve_points_and_seconds"
    assert out["deepest_usd"] == pytest.approx(200.0)
    assert out["deepest_pct"] == pytest.approx(18.181818, abs=1e-6)
    assert out["deepest_recovered"] is True
    assert out["episodes"] == 1
    assert out["open"] == 0
    assert out["skipped"] == 0
    assert _only(out) == {"median": 3.0, "p90": 3.0, "n": 1}
    assert out["drawdown_recovery_sec"] == {"median": 300.0, "p90": 300.0, "n": 1}


def test_a_curve_without_timestamps_keeps_its_lengths_in_points_and_drops_the_seconds():
    """Nothing recorded a time, so the section omits the seconds key rather than reporting zero."""
    out = drawdown_recovery(_curve(1100.0, 900.0, 950.0, 1100.0, 1200.0), starting_value=1000.0)

    assert _only(out) == {"median": 3.0, "p90": 3.0, "n": 1}
    assert "drawdown_recovery_sec" not in out


def test_a_drawdown_that_never_came_back_is_counted_and_carries_no_length():
    """An open drawdown is a reading, not a duration: it has not finished being one."""
    out = drawdown_recovery(_curve((1200.0, 10.0), (1000.0, 20.0), (1100.0, 30.0)),
                            starting_value=1000.0)

    assert out["deepest_usd"] == pytest.approx(200.0)
    assert out["deepest_pct"] == pytest.approx(16.666667, abs=1e-6)
    assert out["deepest_recovered"] is False
    assert out["episodes"] == 0
    assert out["open"] == 1
    assert out["drawdown_recovery_trades"] == {"median": None, "p90": None, "n": 0}
    assert "drawdown_recovery_sec" not in out


def test_a_curve_that_only_rises_measured_a_drawdown_of_zero_rather_than_nothing():
    """Zero is a measurement here: the curve was observed and never dropped."""
    out = drawdown_recovery(_curve(1000.0, 1100.0, 1200.0))

    assert out["deepest_usd"] == 0.0
    assert out["deepest_pct"] == 0.0
    assert out["deepest_recovered"] is None
    assert out["episodes"] == 0
    assert out["open"] == 0


def test_an_empty_curve_measures_nothing_at_all():
    out = drawdown_recovery([])

    assert out["deepest_usd"] is None
    assert out["deepest_pct"] is None
    assert out["deepest_recovered"] is None
    assert out["episodes"] == 0
    assert out["open"] == 0
    assert out["skipped"] == 0
    assert out["drawdown_recovery_trades"] == {"median": None, "p90": None, "n": 0}
    assert json.loads(json.dumps(out, allow_nan=False))["deepest_usd"] is None


def test_a_peak_at_zero_reports_the_dollars_and_no_percentage():
    """A share of a non-positive peak is not a smaller drawdown, it is no percentage at all."""
    out = drawdown_recovery(_curve(0.0, -50.0, 0.0), starting_value=0.0)

    assert out["deepest_usd"] == pytest.approx(50.0)
    assert out["deepest_pct"] is None
    assert out["deepest_recovered"] is True
    assert out["episodes"] == 1


def test_points_without_a_usable_value_are_skipped_and_counted():
    """One malformed point cannot drag a peak down or invent a trough, and it says how many did not."""
    dirty = _curve((1100.0, 100.0), (float("nan"), 150.0), (900.0, 200.0), (None, 250.0),
                   (950.0, 300.0), ("abc", 350.0), (1100.0, 400.0), (float("inf"), 450.0),
                   (1200.0, 500.0))

    out = drawdown_recovery(dirty, starting_value=1000.0)

    assert out["skipped"] == 4
    assert out["deepest_usd"] == pytest.approx(200.0)
    # A dropped point is not a curve point: the episode still spans the three usable ones.
    assert _only(out) == {"median": 3.0, "p90": 3.0, "n": 1}
    assert json.loads(json.dumps(out, allow_nan=False))["deepest_usd"] == pytest.approx(200.0)


def test_the_medians_read_the_regained_drawdowns_only():
    """Two regained episodes of different lengths, one still open, and an interpolated p90."""
    curve = _curve((110.0, 0.0), (90.0, 10.0), (100.0, 20.0), (110.0, 30.0),      # 3 points long
                   (120.0, 40.0), (100.0, 50.0), (105.0, 60.0), (110.0, 70.0),    # 4 points long
                   (121.0, 80.0), (130.0, 90.0), (125.0, 100.0))                  # still open

    out = drawdown_recovery(curve, starting_value=100.0)

    assert out["episodes"] == 2
    assert out["open"] == 1
    assert out["drawdown_recovery_trades"] == {"median": 3.5, "p90": 3.9, "n": 2}
    assert out["drawdown_recovery_sec"] == {"median": 35.0, "p90": 39.0, "n": 2}


def test_the_episodes_are_the_drawdowns_the_msummary_counts():
    """The list is the summary's own working: every peak-to-recovery run, in curve order."""
    episodes = drawdown_episodes(RECOVERY, starting_value=1000.0)

    assert len(episodes) == 1
    only = episodes[0]
    assert only["peak"] == pytest.approx(1100.0)
    assert only["trough"] == pytest.approx(900.0)
    assert only["depth_usd"] == pytest.approx(200.0)
    assert only["depth_pct"] == pytest.approx(18.181818, abs=1e-6)
    assert only["recovered"] is True
    assert only["points"] == 3
    assert only["seconds"] == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# The skewness half: where the tail risk sits
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("values, expected", [
    ([1.0, 1.0, 1.0, 1.0, -10.0], -1.5),      # a lone large loss: the downside-tail shape
    ([-1.0, -1.0, -1.0, -1.0, 10.0], 1.5),    # a lone large win: the upside-tail shape
    ([-1.0, 0.0, 1.0], 0.0),                  # symmetric about the mean
])
def test_skewness_is_the_third_standardized_moment(values, expected):
    assert pnl_skewness(values)["pnl_skewness"] == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("values", [[1.0, 2.0], [2.0, 2.0, 2.0], [], [5.0]])
def test_skewness_needs_three_distinct_observations(values):
    """Two values have no third moment to measure, and a flat sample has no spread to divide by.

    Reporting `0.0` for either would read as "roughly symmetric" -- a verdict drawn from evidence
    that does not exist.
    """
    out = pnl_skewness(values)

    assert out["pnl_skewness"] is None
    assert out["band"] == "unmeasured"
    assert out["n"] == len(values)


def test_the_settled_skewness_bands_are_inclusive_where_the_ticket_says_so():
    assert skewness_band(0.000001) == "good"
    assert skewness_band(0.0) == "average"        # symmetric is not an upside tail
    assert skewness_band(-0.5) == "average"
    assert skewness_band(-0.500001) == "bad"
    assert skewness_band(None) == "unmeasured"    # never `bad`: nobody could judge it


def test_the_skewness_reading_carries_its_band_and_its_words():
    out = pnl_skewness([1.0, 1.0, 1.0, 1.0, -10.0])

    assert set(out) == {"pnl_skewness", "band", "meaning", "n"}
    assert out["band"] == "bad"
    assert out["meaning"] == SKEWNESS_BAND_WORDS["bad"]
    assert out["n"] == 5


def test_a_malformed_return_is_dropped_rather_than_measured_as_zero():
    dirty = [1.0, 1.0, float("nan"), 1.0, None, 1.0, -10.0, float("inf"), "abc"]

    assert pnl_skewness(dirty)["pnl_skewness"] == pnl_skewness([1.0, 1.0, 1.0, 1.0, -10.0])["pnl_skewness"]
    assert pnl_skewness(dirty)["n"] == 5


# ---------------------------------------------------------------------------
# The Sortino half: return per unit of downside pain
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("risk_free, expected", [
    (0.0, 1.2),
    (1.0, 0.1),
    (11.0, -0.91227),     # every excess return is now a loss, and the reading says so
])
def test_sortino_is_measured_against_the_risk_free_rate_it_is_given(risk_free, expected):
    assert sortino_ratio([-1.0, -1.0, -1.0, -1.0, 10.0], risk_free) == pytest.approx(expected, abs=1e-6)


def test_sortino_separates_the_tail_that_matters():
    """The same mean either way; the downside deviation is what moves."""
    assert sortino_ratio([2.0, 2.0, 2.0, 2.0, -5.0]) == pytest.approx(0.12, abs=1e-6)


@pytest.mark.parametrize("values, risk_free", [
    ([], 0.0),
    ([3.0], 0.0),
    ([2.0, 2.0, 2.0], 0.0),      # no spread to divide by
    ([1.0, 2.0, 3.0], 0.0),      # no downside to be paid for
    ([-1.0, 2.0], float("nan")),
])
def test_sortino_declines_to_answer_without_the_evidence(values, risk_free):
    assert sortino_ratio(values, risk_free) is None
    assert sortino(values, risk_free)["band"] == "unmeasured"


def test_the_shipped_risk_free_rate_is_named_on_the_reading():
    """The operator sees the assumption the number was computed under, not just the number."""
    out = sortino([-1.0, -1.0, -1.0, -1.0, 10.0])

    assert out["risk_free_pct_per_close"] == RISK_FREE_PCT_PER_CLOSE == 0.0
    assert out["sortino_ratio"] == pytest.approx(1.2, abs=1e-6)
    assert out["band"] == "average"
    assert out["meaning"] == SORTINO_BAND_WORDS["average"]
    assert out["n"] == 5


def test_the_settled_sortino_bands_are_inclusive_where_the_ticket_says_so():
    assert sortino_band(2.0) == "good"
    assert sortino_band(1.999999) == "average"
    assert sortino_band(1.0) == "average"
    assert sortino_band(0.999999) == "bad"
    assert sortino_band(-0.23) == "bad"
    assert sortino_band(None) == "unmeasured"


# ---------------------------------------------------------------------------
# The oracle: the same reading, from `kpi`'s own Sortino
# ---------------------------------------------------------------------------

def _oracle_closes(returns: list[float]) -> list[dict]:
    """Closes whose percentage return IS the number handed in, so the two series are the same."""
    return [{"realized_pnl": r, "cost_basis": 100.0} for r in returns]


@pytest.mark.parametrize("returns", [[-1.0, -1.0, -1.0, -1.0, 10.0], [2.0, 2.0, 2.0, 2.0, -5.0]])
def test_sortino_matches_the_kpi_oracle_when_the_risk_free_rate_is_zero(returns):
    oracle = kpi.compute_trade_analytics(_oracle_closes(returns), 100.0, [], [])

    assert sortino_ratio(returns, 0.0) == pytest.approx(oracle["sortino_ratio"], abs=1e-6)


def test_the_two_ratios_disagree_exactly_where_the_skew_says_they_should():
    """An upside surge flatters Sortino; a downside tail drags it below Sharpe."""
    upside = [-1.0, -1.0, -1.0, -1.0, 10.0]
    downside = [2.0, 2.0, 2.0, 2.0, -5.0]

    upside_oracle = kpi.compute_trade_analytics(_oracle_closes(upside), 100.0, [], [])
    downside_oracle = kpi.compute_trade_analytics(_oracle_closes(downside), 100.0, [], [])

    assert sortino_ratio(upside, 0.0) > upside_oracle["sharpe_ratio"]
    assert sortino_ratio(downside, 0.0) < downside_oracle["sharpe_ratio"]


# ---------------------------------------------------------------------------
# The oracle: the same reading, from `kpi`'s own drawdown walk
# ---------------------------------------------------------------------------

def test_the_drawdown_agrees_with_the_kpi_oracle_on_a_clean_curve():
    values = [1100.0, 900.0, 950.0, 1100.0, 1200.0]
    out = drawdown_recovery(_curve(*values), starting_value=1000.0)
    oracle = kpi.compute_trade_analytics([], 1000.0, [{"v": v} for v in values], [])

    assert out["deepest_usd"] == pytest.approx(oracle["max_drawdown_usd"])
    assert out["deepest_pct"] == pytest.approx(oracle["max_drawdown_pct"])


def test_the_two_deliberate_differences_from_the_kpi_walk():
    """Skipped-invalid and peak-at-zero, pinned so the divergence stays a decision, not a drift."""
    oracle = kpi.compute_trade_analytics(
        [], 1000.0, [{"v": 900.0}, {"v": None}, {"v": 950.0}], [])

    # `kpi` reads the unrecorded point as 0.0 -- a crash to zero that never happened.
    assert oracle["max_drawdown_usd"] == pytest.approx(1000.0)
    assert drawdown_recovery(_curve(900.0, None, 950.0),
                             starting_value=1000.0)["deepest_usd"] == pytest.approx(100.0)

    # A peak at or below zero: `kpi` answers 0.0%, this section answers None.
    zero_peak = kpi.compute_trade_analytics([], 0.0, [{"v": 0.0}, {"v": -50.0}], [])
    assert zero_peak["max_drawdown_pct"] == 0.0
    assert drawdown_recovery(_curve(0.0, -50.0), starting_value=0.0)["deepest_pct"] is None


# ---------------------------------------------------------------------------
# The module's promises
# ---------------------------------------------------------------------------

def test_importing_this_module_pulls_in_neither_the_registry_nor_kpi():
    """The pure module's promise: no import side effects."""
    code = (
        "import sys\n"
        "import core_brain.statistical_analytics\n"
        "assert 'core_brain.kpi' not in sys.modules, 'kpi loaded'\n"
        "assert 'core_brain.order_registry' not in sys.modules, 'registry loaded'\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr


def test_this_file_holds_no_second_copy_of_the_reused_statistics():
    """Reuse is the ticket's rule: one CI helper and one SQN, both the module's own."""
    source = open(__file__, encoding="utf-8").read()

    # Assembled so this very assertion is not the match it is looking for.
    for definition in ("def " + "_mean_pnl_ci", "def " + "system_quality_number",
                       "1." + "96"):
        assert definition not in source
    assert kpi._mean_pnl_ci is sa.mean_pnl_ci
    assert sa.system_quality_number.__module__ == "core_brain.statistical_analytics"

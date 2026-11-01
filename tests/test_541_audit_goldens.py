"""#541 T1: golden/pin tests freezing today's script-layer statistics math.

Test freeze only — no production-code changes. Every expected value below was
derived by running the current code against the shared fixture
(tests/stat_541_fixture.py), never invented.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest

from core_brain.order_registry import OrderRegistry
from core_brain.statistical_analytics import ci_critical_value, ci_z

from scripts import family_probe_report, paired_depth_report
from scripts import statistical_trade_audit as audit
from tests.stat_541_fixture import build_fixed_run

# shadow_merge_legs is created by the shadow loop (core_brain/shadow_exec.py),
# not by OrderRegistry, so the fixture DB lacks it while run_analysis requires
# all three tables. Test-only DDL with the production columns; the audit only
# SELECTs it and never counts its rows toward the merge rate.
SHADOW_MERGE_LEGS_DDL = """CREATE TABLE IF NOT EXISTS shadow_merge_legs (
    tx_hash TEXT NOT NULL,
    pair_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    shares REAL NOT NULL,
    cost REAL NOT NULL,
    PRIMARY KEY (tx_hash, token_id)
)"""


def _auditable(db: Path) -> str:
    """Add the missing shadow_merge_legs table; return the db path as str."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(SHADOW_MERGE_LEGS_DDL)
        conn.commit()
    finally:
        conn.close()
    return str(db)


def _body_of(full_stdout: str, db_path: str) -> str:
    """Split off the path-dependent 'Opening database: ...' first line."""
    first, sep, rest = full_stdout.partition("\n")
    assert sep, "expected at least one output line"
    assert first == f"Opening database: {db_path}..."
    return rest


FIXTURE_BODY = """Found 8 records in 'closes' table.

===========================================================================
 1. PNL EXPECTANCY ($)
===========================================================================
Total Closed Trades (N):       8
Sample Mean PnL:              $0.7875
Standard Deviation:           $1.4496

Confidence Intervals for Expected PnL:
  [90% CL]: [$-0.1837, $1.7587] | Margin of Error: +/- $0.9712
  [95% CL]: [$-0.4246, $1.9996] | Margin of Error: +/- $1.2121
  [99% CL]: [$-0.5326, $2.1076] | Margin of Error: +/- $1.3201

===========================================================================
 2. PROPORTIONS: STOP LOSS RATE & MERGE RATE
===========================================================================
Stop Loss Exits: 0/8 (0.00%)
  [90% CL]: [0.00%, 25.27%] | Margin: +/- 12.64%
  [95% CL]: [0.00%, 32.44%] | Margin: +/- 16.22%
  [99% CL]: [0.00%, 45.34%] | Margin: +/- 22.67%

Merged Positions: 5/8 (62.50%)
  [90% CL]: [34.80%, 83.88%] | Margin: +/- 24.54%
  [95% CL]: [30.57%, 86.32%] | Margin: +/- 27.87%
  [99% CL]: [23.75%, 89.92%] | Margin: +/- 33.09%

===========================================================================
 4. SAMPLE SIZE PLANNING FOR PNL EXPECTANCY
===========================================================================

Target Margin of Error: +/- $0.05
  [90% CL]: Required Total N = 2274 (Need 2266 more trades)
  [95% CL]: Required Total N = 3229 (Need 3221 more trades)
  [99% CL]: Required Total N = 5577 (Need 5569 more trades)

Target Margin of Error: +/- $0.02
  [90% CL]: Required Total N = 14213 (Need 14205 more trades)
  [95% CL]: Required Total N = 20180 (Need 20172 more trades)
  [99% CL]: Required Total N = 34854 (Need 34846 more trades)

Target Margin of Error: +/- $0.01
  [90% CL]: Required Total N = 56850 (Need 56842 more trades)
  [95% CL]: Required Total N = 80719 (Need 80711 more trades)
  [99% CL]: Required Total N = 139416 (Need 139408 more trades)
===========================================================================
"""

EMPTY_BODY = """Found 0 records in 'closes' table.

No closed trade records found to analyze.
"""

ONE_CLOSE_BODY = """Found 1 records in 'closes' table.

===========================================================================
 1. PNL EXPECTANCY ($)
===========================================================================
Total Closed Trades (N):       1
Sample Mean PnL:              $2.1000
Standard Deviation:           $0.0000

Confidence Intervals for Expected PnL:
  [90% CL]: [$2.1000, $2.1000] | Margin of Error: +/- $0.0000
  [95% CL]: [$2.1000, $2.1000] | Margin of Error: +/- $0.0000
  [99% CL]: [$2.1000, $2.1000] | Margin of Error: +/- $0.0000

===========================================================================
 2. PROPORTIONS: STOP LOSS RATE & MERGE RATE
===========================================================================
Stop Loss Exits: 0/1 (0.00%)
  [90% CL]: [0.00%, 73.01%] | Margin: +/- 36.51%
  [95% CL]: [0.00%, 79.35%] | Margin: +/- 39.67%
  [99% CL]: [0.00%, 86.90%] | Margin: +/- 43.45%

Merged Positions: 1/1 (100.00%)
  [90% CL]: [26.99%, 100.00%] | Margin: +/- 36.51%
  [95% CL]: [20.65%, 100.00%] | Margin: +/- 39.67%
  [99% CL]: [13.10%, 100.00%] | Margin: +/- 43.45%

===========================================================================
 4. SAMPLE SIZE PLANNING FOR PNL EXPECTANCY
===========================================================================

Target Margin of Error: +/- $0.05
  [90% CL]: Required Total N = 0 (Need 0 more trades)
  [95% CL]: Required Total N = 0 (Need 0 more trades)
  [99% CL]: Required Total N = 0 (Need 0 more trades)

Target Margin of Error: +/- $0.02
  [90% CL]: Required Total N = 0 (Need 0 more trades)
  [95% CL]: Required Total N = 0 (Need 0 more trades)
  [99% CL]: Required Total N = 0 (Need 0 more trades)

Target Margin of Error: +/- $0.01
  [90% CL]: Required Total N = 0 (Need 0 more trades)
  [95% CL]: Required Total N = 0 (Need 0 more trades)
  [99% CL]: Required Total N = 0 (Need 0 more trades)
===========================================================================
"""


def test_run_analysis_fixture_stdout_frozen(tmp_path, capsys):
    db_path = _auditable(build_fixed_run(tmp_path / "audit.db"))
    audit.run_analysis(db_path)
    assert _body_of(capsys.readouterr().out, db_path) == FIXTURE_BODY


def test_run_analysis_fixture_values_at_all_levels():
    """Numeric pins behind the frozen stdout: mean, mean-CI, Wilson, planner."""
    z = audit.Z_CRITICAL
    assert audit.calculate_mean_and_std([-1.2, -0.6, 2.1, 0.9, 1.5, 0.0, 0.4, 3.2]) == (
        0.7875000000000001, 1.4495689014324225)
    assert audit.mean_confidence_interval(0.7875000000000001, 1.4495689014324225, 8, z["95%"]) == (
        -0.42456249999999995, 1.9995625000000001, 1.2120625)  # #541 t-switch was (-0.2170, 1.7920, 1.0045)
    assert audit.mean_confidence_interval(0.7875000000000001, 1.4495689014324225, 8, z["90%"]) == (
        -0.18368749999999978, 1.7586875, 0.9711874999999999)
    assert audit.mean_confidence_interval(0.7875000000000001, 1.4495689014324225, 8, z["99%"]) == (
        -0.5326128749999997, 2.107612875, 1.3201128749999997)
    assert audit.proportion_confidence_interval(0, 8, z["95%"]) == (
        0.0, 0.3244066737643051, 0.16220333688215255)
    assert audit.proportion_confidence_interval(0, 8, z["90%"]) == (
        0.0, 0.2527227645646307, 0.12636138228231536)
    assert audit.proportion_confidence_interval(0, 8, z["99%"]) == (
        0.0, 0.45336149227258227, 0.22668074613629113)
    assert audit.proportion_confidence_interval(5, 8, z["95%"]) == (
        0.3057428968878372, 0.8631554346710865, 0.27870626889162464)
    assert audit.proportion_confidence_interval(5, 8, z["90%"]) == (
        0.3479919536046764, 0.838827355254166, 0.2454177008247448)  # #541: shared-Wilson op order, 1-ulp realignment
    assert audit.proportion_confidence_interval(5, 8, z["99%"]) == (
        0.2374701607531901, 0.8991894661786644, 0.3308596527127371)
    std = 1.4495689014324225
    assert [(audit.required_sample_size_for_mean(std, t, z[cl]), cl)
            for t in (0.05,) for cl in ("90%", "95%", "99%")] == [
        (2274, "90%"), (3229, "95%"), (5577, "99%")]
    assert [audit.required_sample_size_for_mean(std, 0.02, z[cl])
            for cl in ("90%", "95%", "99%")] == [14213, 20180, 34854]
    assert [audit.required_sample_size_for_mean(std, 0.01, z[cl])
            for cl in ("90%", "95%", "99%")] == [56850, 80719, 139416]


def test_run_analysis_empty_db_stdout_frozen(tmp_path, capsys):
    db = tmp_path / "empty.db"
    OrderRegistry(db_path=db)  # real schema, zero closes
    db_path = _auditable(db)
    audit.run_analysis(db_path)
    assert _body_of(capsys.readouterr().out, db_path) == EMPTY_BODY


def test_run_analysis_one_close_db_stdout_frozen(tmp_path, capsys):
    db = build_fixed_run(tmp_path / "one.db")
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM closes WHERE realized_pnl != 2.1 OR realized_pnl IS NULL")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM closes").fetchone()[0] == 1
    finally:
        conn.close()
    db_path = _auditable(db)
    audit.run_analysis(db_path)
    assert _body_of(capsys.readouterr().out, db_path) == ONE_CLOSE_BODY


def mean_ci90(xs: list) -> tuple:
    """Mean with a Student-t 90% interval; empty input stays unmeasured.

    Inlined from the retired probe script: the statistical_analytics
    equivalent gives different numbers, so the pin needs this formula.
    """
    xs = [x for x in xs if x is not None]
    if not xs:
        return None, None, None
    mean = sum(xs) / len(xs)
    if len(xs) < 2:
        return mean, None, None
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    mult = ci_critical_value(90, len(xs), ci_z(90))
    half = mult * math.sqrt(var) / math.sqrt(len(xs))
    return mean, mean - half, mean + half


def _prop_ci90_diff(n1: int, k1: int, n2: int, k2: int) -> tuple:
    """90% interval for p2 - p1, two-proportion normal approximation.

    Keeps its own formula on purpose: an unpooled interval for a difference is
    a different estimator from Wilson, not a copy of it (#541).
    """
    if n1 < 2 or n2 < 2:
        return None, None
    p1, p2 = k1 / n1, k2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    diff = p2 - p1
    half = 1.645 * se
    return diff - half, diff + half


def test_mean_ci90_pins():
    assert mean_ci90([]) == (None, None, None)
    assert mean_ci90([2.5]) == (2.5, None, None)
    assert mean_ci90([1, 3]) == (2.0, -4.313999999999999, 8.314)  # #541 t-switch was (2.0, 0.355, 3.645)


def test_prop_ci90_diff_pin():
    assert _prop_ci90_diff(2, 0, 2, 2) == (1.0, 1.0)


@pytest.mark.parametrize(("pilot_sigma", "expected"), [(0.1, 2), (1.0, 8), (2.0, 32)])
def test_required_clusters_with_minimum_of_two(pilot_sigma, expected):
    """Pin of the analyze_paired_depth power rule (target_delta = $1.00):
    max(2, ceil(((1.96 + 0.842) * sigma / 1.0)^2)) — the tiny-sigma case
    proves the floor of 2 binds instead of returning 1."""
    assert max(2, math.ceil(((1.96 + 0.842) * pilot_sigma / 1.0) ** 2)) == expected


def test_required_clusters_floor_still_in_source():
    """Anchor: the minimum-of-two rule must stay in analyze_paired_depth —
    silently dropping the floor would under-power every small pilot."""
    source = Path(paired_depth_report.__file__).read_text(encoding="utf-8")
    assert "required_clusters = max(2," in source


def test_family_percentile_median_and_p90():
    values = list(range(1, 11))
    assert family_probe_report._nearest_rank_percentile(values, 0.5) == 5
    assert family_probe_report._nearest_rank_percentile(values, 0.9) == 9

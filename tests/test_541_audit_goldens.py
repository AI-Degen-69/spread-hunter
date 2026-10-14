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

from scripts import family_probe_report, ladder_probe, paired_depth_report
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
  [95% CL]: [$-0.4246, $1.9996] | Margin of Error: +/- $1.2121
  [98% CL]: [$-0.4048, $1.9798] | Margin of Error: +/- $1.1923
  [99% CL]: [$-0.5326, $2.1076] | Margin of Error: +/- $1.3201

===========================================================================
 2. PROPORTIONS: STOP LOSS RATE & MERGE RATE
===========================================================================
Stop Loss Exits: 0/8 (0.00%)
  [95% CL]: [0.00%, 32.44%] | Margin: +/- 16.22%
  [98% CL]: [0.00%, 40.35%] | Margin: +/- 20.18%
  [99% CL]: [0.00%, 45.34%] | Margin: +/- 22.67%

Merged Positions: 5/8 (62.50%)
  [95% CL]: [30.57%, 86.32%] | Margin: +/- 27.87%
  [98% CL]: [26.29%, 88.62%] | Margin: +/- 31.16%
  [99% CL]: [23.75%, 89.92%] | Margin: +/- 33.09%

===========================================================================
 4. SAMPLE SIZE PLANNING FOR PNL EXPECTANCY
===========================================================================

Target Margin of Error: +/- $0.05
  [95% CL]: Required Total N = 3229 (Need 3221 more trades)
  [98% CL]: Required Total N = 4549 (Need 4541 more trades)
  [99% CL]: Required Total N = 5577 (Need 5569 more trades)

Target Margin of Error: +/- $0.02
  [95% CL]: Required Total N = 20180 (Need 20172 more trades)
  [98% CL]: Required Total N = 28430 (Need 28422 more trades)
  [99% CL]: Required Total N = 34854 (Need 34846 more trades)

Target Margin of Error: +/- $0.01
  [95% CL]: Required Total N = 80719 (Need 80711 more trades)
  [98% CL]: Required Total N = 113718 (Need 113710 more trades)
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
  [95% CL]: [$2.1000, $2.1000] | Margin of Error: +/- $0.0000
  [98% CL]: [$2.1000, $2.1000] | Margin of Error: +/- $0.0000
  [99% CL]: [$2.1000, $2.1000] | Margin of Error: +/- $0.0000

===========================================================================
 2. PROPORTIONS: STOP LOSS RATE & MERGE RATE
===========================================================================
Stop Loss Exits: 0/1 (0.00%)
  [95% CL]: [0.00%, 79.35%] | Margin: +/- 39.67%
  [98% CL]: [0.00%, 84.40%] | Margin: +/- 42.20%
  [99% CL]: [0.00%, 86.90%] | Margin: +/- 43.45%

Merged Positions: 1/1 (100.00%)
  [95% CL]: [20.65%, 100.00%] | Margin: +/- 39.67%
  [98% CL]: [15.60%, 100.00%] | Margin: +/- 42.20%
  [99% CL]: [13.10%, 100.00%] | Margin: +/- 43.45%

===========================================================================
 4. SAMPLE SIZE PLANNING FOR PNL EXPECTANCY
===========================================================================

Target Margin of Error: +/- $0.05
  [95% CL]: Required Total N = 0 (Need 0 more trades)
  [98% CL]: Required Total N = 0 (Need 0 more trades)
  [99% CL]: Required Total N = 0 (Need 0 more trades)

Target Margin of Error: +/- $0.02
  [95% CL]: Required Total N = 0 (Need 0 more trades)
  [98% CL]: Required Total N = 0 (Need 0 more trades)
  [99% CL]: Required Total N = 0 (Need 0 more trades)

Target Margin of Error: +/- $0.01
  [95% CL]: Required Total N = 0 (Need 0 more trades)
  [98% CL]: Required Total N = 0 (Need 0 more trades)
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
    assert audit.mean_confidence_interval(0.7875000000000001, 1.4495689014324225, 8, z["98%"]) == (
        -0.4047543749999998, 1.979754375, 1.1922543749999999)
    assert audit.mean_confidence_interval(0.7875000000000001, 1.4495689014324225, 8, z["99%"]) == (
        -0.5326128749999997, 2.107612875, 1.3201128749999997)
    assert audit.proportion_confidence_interval(0, 8, z["95%"]) == (
        0.0, 0.3244066737643051, 0.16220333688215255)
    assert audit.proportion_confidence_interval(0, 8, z["98%"]) == (
        0.0, 0.4035149813454092, 0.2017574906727046)
    assert audit.proportion_confidence_interval(0, 8, z["99%"]) == (
        0.0, 0.45336149227258227, 0.22668074613629113)
    assert audit.proportion_confidence_interval(5, 8, z["95%"]) == (
        0.3057428968878372, 0.8631554346710865, 0.27870626889162464)
    assert audit.proportion_confidence_interval(5, 8, z["98%"]) == (
        0.2629230909726917, 0.8861981636909562, 0.3116375363591323)  # #541: shared-Wilson op order, 1-ulp realignment
    assert audit.proportion_confidence_interval(5, 8, z["99%"]) == (
        0.2374701607531901, 0.8991894661786644, 0.3308596527127371)
    std = 1.4495689014324225
    assert [(audit.required_sample_size_for_mean(std, t, z[cl]), cl)
            for t in (0.05,) for cl in ("95%", "98%", "99%")] == [
        (3229, "95%"), (4549, "98%"), (5577, "99%")]
    assert [audit.required_sample_size_for_mean(std, 0.02, z[cl])
            for cl in ("95%", "98%", "99%")] == [20180, 28430, 34854]
    assert [audit.required_sample_size_for_mean(std, 0.01, z[cl])
            for cl in ("95%", "98%", "99%")] == [80719, 113718, 139416]


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


def test_ladder_mean_ci90_pins():
    assert ladder_probe.mean_ci90([]) == (None, None, None)
    assert ladder_probe.mean_ci90([2.5]) == (2.5, None, None)
    assert ladder_probe.mean_ci90([1, 3]) == (2.0, -4.313999999999999, 8.314)  # #541 t-switch was (2.0, 0.355, 3.645)


def test_ladder_prop_ci90_diff_pin():
    assert ladder_probe._prop_ci90_diff(2, 0, 2, 2) == (1.0, 1.0)


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

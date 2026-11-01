"""Tests for the Tournament Cockpit Decision Gates tab (per-arm model).

Every arm gets its own verdict from its own gates; the only cross-arm
readout is the run-level volume check, which is informational for the run
and never a verdict on any arm. There is no test arm, no overall verdict,
and no fixed-sample constant: expectancy sufficiency sizes from each arm's
own closes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.writes_real_state

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import tournament_cockpit_server as cockpit
from tests.test_tournament_cockpit_arms import _CockpitHandlerStub


def _exp_row(base_n, sufficient, levels):
    return {
        "key": "expectancy_usd",
        "label": "PnL Expectancy ($)",
        "type": "mean",
        "base": "closes",
        "base_n": base_n,
        "sufficient_95": sufficient,
        "levels": levels,
    }


def _levels_95(req, rem, prog):
    return [{"confidence_pct": 95, "required_n": req,
             "remaining_n": rem, "progress_pct": prog}]


def test_compute_decision_gates_empty_data():
    arms = [
        {"name": "control", "status": "missing_db", "trades_count": 0, "total_pnl": 0.0},
        {"name": "balanced-queue5", "status": "missing_db", "trades_count": 0, "total_pnl": 0.0},
    ]
    res = cockpit.compute_decision_gates(arms, "843")
    assert res["tourney_key"] == "843"
    assert res["total_trades"] == 0
    assert len(res["arm_verdicts"]) == 2
    for v in res["arm_verdicts"]:
        assert v["verdict"] == "PENDING REHEARSAL DATA"
        assert v["gates"]["power"]["status"] == "NO_DATA"
        assert v["gates"]["sharpe"]["status"] == "NO_DATA"
    assert res["volume_check"]["status"] == "NO_DATA"
    # No run-level verdict, no test subject.
    assert "overall_verdict" not in res
    assert "test_arm_label" not in res
    assert "target_n" not in res


def test_each_arm_gets_own_verdict_from_own_gates():
    arms = [
        {
            "name": "control",
            "status": "ok",
            "trades_count": 190,
            "total_pnl": 12.0,
            "sharpe_ratio": 1.80,
            "profit_factor": 1.90,
            "max_drawdown": 5.20,
            "merge_rate": 70.0,
        },
        {
            "name": "balanced-queue5",
            "status": "ok",
            "trades_count": 180,
            "total_pnl": 21.0,
            "sharpe_ratio": 2.85,
            "profit_factor": 2.90,
            "max_drawdown": 1.85,
            "merge_rate": 84.5,
            # The arm's own expectancy row: not yet sufficient at 95%,
            # sized from its own stdev -- the required N adapts to the arm.
            "sufficiency_rows": [
                _exp_row(180, False, [
                    {"confidence_pct": 90, "required_n": 1420, "remaining_n": 1240, "progress_pct": 13},
                    {"confidence_pct": 95, "required_n": 1800, "remaining_n": 1620, "progress_pct": 10},
                    {"confidence_pct": 99, "required_n": 3100, "remaining_n": 2920, "progress_pct": 6},
                ]),
            ],
        },
    ]
    res = cockpit.compute_decision_gates(arms, "843")
    by_name = {v["name"]: v for v in res["arm_verdicts"]}
    # The weak-control arm fails its own risk gates on its own numbers.
    assert by_name["control"]["verdict"] == "REJECTED: CRITICAL RISK GATES FAILED"
    # The healthy arm holds risk shape while its own expectancy row unlocks
    # on its own closes -- its verdict never mentions the other arm.
    healthy = by_name["balanced-queue5"]
    assert healthy["verdict"] == "PROVISIONAL: RISK SHAPE HOLDS"
    assert healthy["gates"]["power"]["status"] == "UNDERPOWERED"
    # The gate target is the arm's own 95% required N, not a fixed constant.
    assert healthy["gates"]["power"]["target"] == 1800
    assert healthy["gates"]["sharpe"]["status"] == "PASS"
    assert healthy["gates"]["pf"]["status"] == "PASS"
    assert healthy["gates"]["mdd"]["status"] == "PASS"
    assert res["volume_check"]["status"] == "HEALTHY"


def test_arm_without_rows_reads_unmeasured():
    """An arm that never attached sufficiency rows is unmeasured, not zero.

    Hand-built arm dicts carry no `sufficiency_rows`; the gate must not
    fabricate a required N for them, and the verdict copy must not cite a
    pending N.
    """
    arms = [
        {
            "name": "queue5-only",
            "status": "ok",
            "trades_count": 170,
            "total_pnl": 9.5,
            "sharpe_ratio": 2.75,
            "profit_factor": 2.60,
            "max_drawdown": 2.10,
            "merge_rate": 81.0,
        },
    ]
    res = cockpit.compute_decision_gates(arms, "843")
    (verdict,) = res["arm_verdicts"]
    assert verdict["gates"]["power"]["status"] == "UNDERPOWERED"
    assert verdict["gates"]["power"]["target"] is None
    assert "Not Yet Measurable" in verdict["gates"]["power"]["text"]
    assert verdict["verdict"] == "PROVISIONAL: RISK SHAPE HOLDS"
    assert "not yet measurable" in verdict["desc"]


def test_sufficiency_95_summary_verdicts():
    """The card summary shows each KPI's current calculated value with green/yellow/red tone and swapped column order."""
    rows = [
        {"key": "expectancy_usd", "label": "PnL Expectancy ($)", "base": "closes",
         "base_n": 180, "sufficient_95": False, "value": 0.12, "formatted_value": "+$0.12",
         "levels": [{"confidence_pct": 95, "required_n": 1800,
                      "remaining_n": 1620, "progress_pct": 10}]},
        {"key": "merge_rate", "label": "Merge Rate (%)", "base": "closes",
         "base_n": 180, "sufficient_95": True, "value": 84.5, "formatted_value": "84.5%",
         "levels": [{"confidence_pct": 95, "required_n": 150,
                      "remaining_n": 0, "progress_pct": 100}]},
        {"key": "avg_duration_stop", "label": "Avg Fill-to-Close: Stops",
         "base": "stops", "base_n": 1, "sufficient_95": None, "value": 311.0, "formatted_value": "5m 11s",
         "levels": [{"confidence_pct": 95, "required_n": None,
                      "remaining_n": None, "progress_pct": None}]},
        {"key": "avg_duration_merge", "label": "Avg Fill-to-Close: Merges",
         "base": "merges", "base_n": 0, "sufficient_95": None, "value": None, "formatted_value": None,
         "levels": [{"confidence_pct": 95, "required_n": None,
                      "remaining_n": None, "progress_pct": None}]},
        {"key": "avg_stop_loss_depth_pct", "label": "Avg Stop-Loss Depth (%)",
         "base": "stops", "base_n": 2, "sufficient_95": False, "value": -17.8, "formatted_value": "-17.8%",
         "levels": [{"confidence_pct": 95, "required_n": 80,
                      "remaining_n": 78, "progress_pct": 3}]},
    ]
    out = {s["key"]: s for s in cockpit._sufficiency_95_rows(rows)}
    assert out["expectancy_usd"]["verdict"] == "+$0.12"
    assert out["expectancy_usd"]["badge"] == "kpi-val-warn"
    assert out["expectancy_usd"]["required_95"] == 1800
    assert out["expectancy_usd"]["remaining_95"] == 1620
    assert out["merge_rate"]["verdict"] == "84.5%"
    assert out["merge_rate"]["badge"] == "kpi-val-good"
    # A 1-observation row has no stdev/required_n yet, but still shows its current value
    assert out["avg_duration_stop"]["verdict"] == "5m 11s"
    assert out["avg_duration_stop"]["badge"] == "kpi-val-warn"
    assert out["avg_duration_merge"]["verdict"] == "—"
    assert out["avg_duration_merge"]["required_95"] is None
    assert out["avg_stop_loss_depth_pct"]["verdict"] == "-17.8%"
    assert out["avg_stop_loss_depth_pct"]["badge"] == "kpi-val-bad"

    # Mini-table column order: label -> calculated value -> required_n -> progress bar -> base_n base
    mini_html = cockpit._sufficiency_mini_html(rows)
    exp_row_start = mini_html.index("PnL Expectancy ($)")
    exp_val_pos = mini_html.index("+$0.12", exp_row_start)
    exp_req_pos = mini_html.index("1,800", exp_row_start)
    exp_closes_pos = mini_html.index("180 closes", exp_row_start)
    assert exp_row_start < exp_val_pos < exp_req_pos < exp_closes_pos

    # Matrix cell also shows the current value with kpi-val tone instead of LOCKED or unmeasured when base_n >= 1
    assert "+$0.12" in cockpit._dg_cell_html(rows[0], 95)
    assert "kpi-val-warn" in cockpit._dg_cell_html(rows[0], 95)
    assert "LOCKED" not in cockpit._dg_cell_html(rows[0], 95)
    assert "5m 11s" in cockpit._dg_cell_html(rows[2], 95)
    assert "1 / —" in cockpit._dg_cell_html(rows[2], 95)
    assert "kpi-val-bad" in cockpit._dg_cell_html(rows[4], 95)


def test_volume_starvation_is_run_level_not_a_verdict():
    arms = [
        {
            "name": "balanced-only",
            "status": "ok",
            "trades_count": 200,
            "total_pnl": 20.0,
            "sharpe_ratio": 2.8,
            "profit_factor": 2.6,
            "max_drawdown": 3.5,
        },
        {
            "name": "balanced-queue5",
            "status": "ok",
            "trades_count": 90,  # 55% drop > 40% threshold
            "total_pnl": 5.0,
            "sharpe_ratio": 2.8,
            "profit_factor": 2.6,
            "max_drawdown": 1.5,
        },
    ]
    res = cockpit.compute_decision_gates(arms, "843")
    assert res["volume_check"]["status"] == "STARVATION"
    assert "starvation" in res["volume_check"]["text"].lower()
    # ... but neither arm's verdict mentions starvation: each arm is judged
    # on its own gates, and both hold risk shape here.
    for v in res["arm_verdicts"]:
        assert "STARVATION" not in v["verdict"]
        assert v["verdict"] == "PROVISIONAL: RISK SHAPE HOLDS"


def test_fully_sufficient_arm_clears_on_own_gates():
    arms = [
        {
            "name": "balanced-queue5",
            "status": "ok",
            "trades_count": 2150,
            "total_pnl": 180.0,
            "sharpe_ratio": 2.95,
            "profit_factor": 3.10,
            "max_drawdown": 2.20,
            # Expectancy unlocked on its own closes at 95%: the cleared
            # verdict no longer needs a fixed cross-run constant.
            "sufficiency_rows": [
                _exp_row(2150, True, [
                    {"confidence_pct": 90, "required_n": 1420, "remaining_n": 0, "progress_pct": 100},
                    {"confidence_pct": 95, "required_n": 1800, "remaining_n": 0, "progress_pct": 100},
                    {"confidence_pct": 99, "required_n": 3100, "remaining_n": 950, "progress_pct": 69},
                ]),
            ],
        },
    ]
    res = cockpit.compute_decision_gates(arms, "843")
    (verdict,) = res["arm_verdicts"]
    assert verdict["verdict"] == "CLEARED: ALL GATES PASS AT 95%"
    assert verdict["gates"]["power"]["status"] == "PASS"
    assert verdict["gates"]["power"]["target"] == 1800


def test_rejected_arm_does_not_sink_others():
    arms = [
        {
            "name": "control",
            "status": "ok",
            "trades_count": 190,
            "total_pnl": -8.0,
            "sharpe_ratio": 0.90,
            "profit_factor": 0.80,
            "max_drawdown": 9.50,
        },
        {
            "name": "queue5-only",
            "status": "ok",
            "trades_count": 170,
            "total_pnl": 9.5,
            "sharpe_ratio": 2.75,
            "profit_factor": 2.60,
            "max_drawdown": 2.10,
            "sufficiency_rows": [_exp_row(170, False, _levels_95(1500, 1330, 11))],
        },
    ]
    res = cockpit.compute_decision_gates(arms, "843")
    by_name = {v["name"]: v for v in res["arm_verdicts"]}
    assert by_name["control"]["verdict"] == "REJECTED: CRITICAL RISK GATES FAILED"
    assert by_name["queue5-only"]["verdict"] == "PROVISIONAL: RISK SHAPE HOLDS"


def test_gate_map_names_volume_roles_not_a_test_arm():
    for key in ("843", "799", "905"):
        arm_map = cockpit.DECISION_GATE_ARM_MAP[key]
        assert "test_arm_pattern" not in arm_map
        assert "volume_arm_pattern" in arm_map
        assert arm_map["baseline_arm_names"]
    assert cockpit.DECISION_GATE_ARM_MAP["905"]["baseline_arm_names"] == ["loose-baseline"]


def _render_stats():
    return {
        "tourney_key": "843",
        "tourney_meta": cockpit.TOURNAMENTS["843"],
        "arms": [
            {
                "name": "control", "label": "Control", "status": "ok",
                "port": 8801, "trades_count": 190, "total_pnl": 12.0,
                "sharpe_ratio": 1.80, "profit_factor": 1.90,
                "max_drawdown": 5.20, "merge_rate": 70.0,
                "desc": "Pure control/baseline.",
                "sufficiency_rows": [_exp_row(190, False, [
                    {"confidence_pct": 90, "required_n": 1200, "remaining_n": 1010, "progress_pct": 16},
                    {"confidence_pct": 95, "required_n": 1600, "remaining_n": 1410, "progress_pct": 12},
                    {"confidence_pct": 99, "required_n": 2800, "remaining_n": 2610, "progress_pct": 7},
                ])],
            },
            {
                "name": "balanced-queue5", "label": "Balanced + Queue5",
                "status": "ok", "port": 8804, "trades_count": 180,
                "total_pnl": 21.0, "sharpe_ratio": 2.85,
                "profit_factor": 2.90, "max_drawdown": 1.85,
                "merge_rate": 84.5, "desc": "Balanced preset with queue gate.",
                "sufficiency_rows": [_exp_row(180, False, [
                    {"confidence_pct": 90, "required_n": 1420, "remaining_n": 1240, "progress_pct": 13},
                    {"confidence_pct": 95, "required_n": 1800, "remaining_n": 1620, "progress_pct": 10},
                    {"confidence_pct": 99, "required_n": 3100, "remaining_n": 2920, "progress_pct": 6},
                ])],
            },
        ],
        "elapsed_hours": 1.2,
        "target_hours": 24.0,
        "progress_pct": 5.0,
    }


def test_generate_cockpit_html_contains_per_arm_gates():
    html = cockpit.generate_cockpit_html(_render_stats())
    assert "switchTab('decision-gates', this)" in html
    assert 'id="tab-decision-gates"' in html
    # Run-level bar with the volume check, never a verdict on an arm.
    assert 'id="dg-run-trades"' in html
    assert 'id="dg-volume-badge"' in html
    assert 'id="dg-volume-text"' in html
    # Confidence toggle spans all three discussed levels.
    for conf in ("90", "95", "99"):
        assert f'data-dg-conf="{conf}"' in html
    # Arm filter: one checkbox per arm, all visible by default.
    assert 'id="dg-arm-filter"' in html
    assert 'data-dg-arm-toggle="control"' in html
    assert 'data-dg-arm-toggle="balanced-queue5"' in html
    # KPI x arm matrix at the striving level, with Have/Need cells.
    assert 'id="dg-matrix-table"' in html
    assert "KPI x Arm @ 95%" in html
    assert "PnL Expectancy ($)" in html
    # Each arm carries its own verdict badge; no arm is the test subject.
    assert "Per-Arm Verdicts" in html
    assert "PROVISIONAL: RISK SHAPE HOLDS" in html
    assert "TEST ARM" not in html
    # The old single-arm surface is gone.
    for stale in ("overall_verdict", "test_arm_label", "target_n_display",
                  "dg-test-arm", "dg-gate-sharpe-badge", "Critical Decision Gates"):
        assert stale not in html


def test_cockpit_api_serves_per_arm_gates():
    handler_api = _CockpitHandlerStub("/api/stats?tourney=843")
    handler_api.do_GET()
    assert handler_api.statuses == [200]
    data = json.loads(handler_api.wfile.getvalue())
    assert data["tourney_key"] == "843"
    assert "decision_gates" in data
    dg = data["decision_gates"]
    assert "arm_verdicts" in dg
    assert "volume_check" in dg
    assert "overall_verdict" not in dg
    assert {v["name"] for v in dg["arm_verdicts"]} == {a["name"] for a in data["arms"]}
    for v in dg["arm_verdicts"]:
        assert "verdict" in v and "badge" in v and "gates" in v


def test_meta_and_arms_resolve_same_plan_record():
    meta, minutes = cockpit.resolve_tournament_meta("843")
    arms, err = cockpit.resolve_tournament_arms_with_status("843")
    plan = cockpit._resolve_plan_for_tourney_key("843", meta)
    if plan is not None:
        _, plan_mins = cockpit._plan_identity(plan)
        assert minutes == plan_mins
        assert err is None
        # Check that arms match plan's arms
        record_arms = cockpit._read_arm_record(plan)
        assert len(arms) == len(record_arms)
        assert {a["name"] for a in arms} == {ra["name"] for ra in record_arms}


def test_matrix_helpers_cover_all_confidences_and_unmeasured():
    arms = _render_stats()["arms"]
    # Union of KPI rows across arms, first-seen order.
    kpis = cockpit._dg_kpi_rows(arms)
    assert [k["key"] for k in kpis] == ["expectancy_usd"]
    # Matrix renders at every discussed confidence level; Have/Need cells
    # track the selected level's required N.
    html95 = cockpit._dg_matrix_html(arms, 95)
    assert "KPI x Arm @ 95%" in html95
    assert "1,800" in html95  # balanced-queue5 required N at 95%
    html90 = cockpit._dg_matrix_html(arms, 90)
    assert "KPI x Arm @ 90%" in html90
    assert "1,420" in html90  # same arm, 90% level
    html99 = cockpit._dg_matrix_html(arms, 99)
    assert "3,100" in html99  # same arm, 99% level
    # An arm with no rows reads unmeasured, never zero.
    bare = [{"name": "control", "label": "Control"}]
    assert "unmeasured" in cockpit._dg_matrix_html(bare, 95)
    assert "unmeasured" in cockpit._dg_cell_html(None, 95)
    # Filter exposes every arm.
    filt = cockpit._dg_filter_html(arms)
    assert filt.count("data-dg-arm-toggle") == 2

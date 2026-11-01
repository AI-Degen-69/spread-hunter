"""Tests for the Tournament Cockpit Decision Gates & Rehearsal Status tab."""
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
from tests.test_tournament_cockpit_arms import _CockpitHandlerStub, _stub_stats


def test_compute_decision_gates_empty_data():
    arms = [
        {"name": "control", "status": "missing_db", "trades_count": 0, "total_pnl": 0.0},
        {"name": "balanced-queue5", "status": "missing_db", "trades_count": 0, "total_pnl": 0.0},
    ]
    res = cockpit.compute_decision_gates(arms, "843")
    assert res["overall_verdict"] == "PENDING REHEARSAL DATA"
    assert res["overall_badge"] == "badge-amber"
    assert res["gates"]["power"]["status"] == "NO_DATA"
    assert res["gates"]["sharpe"]["status"] == "NO_DATA"
    assert res["gates"]["pf"]["status"] == "NO_DATA"
    assert res["gates"]["mdd"]["status"] == "NO_DATA"


def test_compute_decision_gates_underpowered_provisional_pass():
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
            "name": "balanced-only",
            "status": "ok",
            "trades_count": 185,
            "total_pnl": 18.0,
            "sharpe_ratio": 2.30,
            "profit_factor": 2.20,
            "max_drawdown": 4.10,
            "merge_rate": 78.0,
        },
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
                {
                    "key": "expectancy_usd",
                    "label": "PnL Expectancy ($)",
                    "type": "mean",
                    "base": "closes",
                    "base_n": 180,
                    "sufficient_95": False,
                    "levels": [
                        {"confidence_pct": 90, "required_n": 1420, "remaining_n": 1240, "progress_pct": 13},
                        {"confidence_pct": 95, "required_n": 1800, "remaining_n": 1620, "progress_pct": 10},
                        {"confidence_pct": 99, "required_n": 3100, "remaining_n": 2920, "progress_pct": 6},
                    ],
                },
            ],
        },
    ]
    res = cockpit.compute_decision_gates(arms, "843")
    assert "PROVISIONAL PASS" in res["overall_verdict"]
    assert res["overall_badge"] == "badge-green"
    assert res["gates"]["power"]["status"] == "UNDERPOWERED"
    # The gate target is the arm's own 95% required N, not a fixed constant.
    assert res["gates"]["power"]["target"] == 1800
    assert res["target_n"] == 1800
    assert res["gates"]["sharpe"]["status"] == "PASS"
    assert res["gates"]["pf"]["status"] == "PASS"
    assert res["gates"]["mdd"]["status"] == "PASS"
    assert res["gates"]["starvation"]["status"] == "HEALTHY"


def test_compute_decision_gates_arm_without_rows_reads_unmeasured():
    """An arm that never attached sufficiency rows is unmeasured, not zero.

    Hand-built and pre-change arm dicts carry no `sufficiency_rows`; the
    gate must not fabricate a required N for them, and the verdict copy
    must say so instead of citing a pending N=0."""
    arms = [
        {
            "name": "control",
            "status": "ok",
            "trades_count": 190,
            "total_pnl": 12.0,
            "sharpe_ratio": 1.80,
            "profit_factor": 1.90,
            "max_drawdown": 5.20,
        },
        {
            "name": "balanced-queue5",
            "status": "ok",
            "trades_count": 180,
            "total_pnl": 21.0,
            "sharpe_ratio": 2.85,
            "profit_factor": 2.90,
            "max_drawdown": 1.85,
        },
    ]
    res = cockpit.compute_decision_gates(arms, "843")
    assert res["gates"]["power"]["status"] == "UNDERPOWERED"
    assert res["gates"]["power"]["target"] is None
    assert res["target_n"] is None
    assert "Not Yet Measurable" in res["gates"]["power"]["text"]
    assert "not yet measurable" in res["overall_desc"]
    assert "N=0" not in res["overall_desc"]


def test_sufficiency_95_summary_verdicts():
    """The card summary unlocks each KPI separately at the striving level."""
    rows = [
        {"key": "expectancy_usd", "label": "PnL Expectancy ($)", "base": "closes",
         "base_n": 180, "sufficient_95": False,
         "levels": [{"confidence_pct": 95, "required_n": 1800,
                        "remaining_n": 1620, "progress_pct": 10}]},
        {"key": "merge_rate", "label": "Merge Rate (%)", "base": "closes",
         "base_n": 180, "sufficient_95": True,
         "levels": [{"confidence_pct": 95, "required_n": 150,
                        "remaining_n": 0, "progress_pct": 100}]},
        {"key": "avg_duration_merge", "label": "Avg Fill-to-Close: Merges",
         "base": "merges", "base_n": 0, "sufficient_95": None,
         "levels": [{"confidence_pct": 95, "required_n": None,
                        "remaining_n": None, "progress_pct": None}]},
    ]
    out = {s["key"]: s for s in cockpit._sufficiency_95_rows(rows)}
    assert out["expectancy_usd"]["verdict"] == "LOCKED"
    assert out["expectancy_usd"]["required_95"] == 1800
    assert out["expectancy_usd"]["remaining_95"] == 1620
    assert out["merge_rate"]["verdict"] == "SUFFICIENT"
    assert out["avg_duration_merge"]["verdict"] == "—"
    assert out["avg_duration_merge"]["required_95"] is None


def test_compute_decision_gates_volume_starvation():
    arms = [
        {
            "name": "balanced-only",
            "status": "ok",
            "trades_count": 200,
            "total_pnl": 20.0,
            "sharpe_ratio": 2.4,
            "profit_factor": 2.1,
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
    assert "VOLUME STARVATION" in res["overall_verdict"]
    assert res["overall_badge"] == "badge-red"
    assert res["gates"]["starvation"]["status"] == "STARVATION"


def test_compute_decision_gates_fully_powered_winner():
    arms = [
        {
            "name": "balanced-only",
            "status": "ok",
            "trades_count": 2200,
            "total_pnl": 120.0,
            "sharpe_ratio": 2.4,
            "profit_factor": 2.1,
            "max_drawdown": 3.8,
        },
        {
            "name": "balanced-queue5",
            "status": "ok",
            "trades_count": 2150,
            "total_pnl": 180.0,
            "sharpe_ratio": 2.95,
            "profit_factor": 3.10,
            "max_drawdown": 2.20,
            # Expectancy unlocked on its own closes at 95%: the winner
            # verdict no longer needs a fixed cross-run constant.
            "sufficiency_rows": [
                {
                    "key": "expectancy_usd",
                    "label": "PnL Expectancy ($)",
                    "type": "mean",
                    "base": "closes",
                    "base_n": 2150,
                    "sufficient_95": True,
                    "levels": [
                        {"confidence_pct": 90, "required_n": 1420, "remaining_n": 0, "progress_pct": 100},
                        {"confidence_pct": 95, "required_n": 1800, "remaining_n": 0, "progress_pct": 100},
                        {"confidence_pct": 99, "required_n": 3100, "remaining_n": 950, "progress_pct": 69},
                    ],
                },
            ],
        },
    ]
    res = cockpit.compute_decision_gates(arms, "843")
    assert "EMPIRICAL WINNER" in res["overall_verdict"]
    assert res["overall_badge"] == "badge-green"
    assert res["gates"]["power"]["status"] == "PASS"
    assert res["gates"]["power"]["target"] == 1800


def test_generate_cockpit_html_contains_decision_gates_tab():
    stats = {
        "tourney_key": "843",
        "tourney_meta": cockpit.TOURNAMENTS["843"],
        "arms": cockpit.ARMS_CONFIG_843,
        "elapsed_hours": 1.2,
        "target_hours": 24.0,
        "progress_pct": 5.0,
    }
    html = cockpit.generate_cockpit_html(stats)
    assert 'switchTab(\'decision-gates\', this)' in html
    assert 'id="tab-decision-gates"' in html
    assert 'Critical Decision Gates (5-Second Skim)' in html
    assert '1. Per-KPI Sufficiency Gate' in html
    assert '2. Sharpe Ratio Gate' in html
    assert '3. Profit Factor Gate' in html
    assert '4. Max Drawdown Gate' in html
    assert '5. Volume Starvation Check' in html
    assert 'Per-Arm Breakdown' in html


def test_cockpit_serves_843_page_and_stats():
    handler = _CockpitHandlerStub("/843")
    handler.do_GET()
    assert handler.statuses == [200]
    body = handler.wfile.getvalue()
    assert b"Balanced" in body

    handler_api = _CockpitHandlerStub("/api/stats?tourney=843")
    handler_api.do_GET()
    assert handler_api.statuses == [200]
    data = json.loads(handler_api.wfile.getvalue())
    assert data["tourney_key"] == "843"
    assert "decision_gates" in data
    # No arm database exists under this checkout: nothing measured, so no
    # fixed constant stands in -- the target reads unmeasured (None).
    assert data["decision_gates"]["target_n"] is None


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


def _healthy_905_arms(test_n=185, test_sharpe=2.90):
    return [
        {"name": "loose-baseline", "status": "ok", "trades_count": 200,
         "total_pnl": 20.0, "sharpe_ratio": 2.40, "profit_factor": 2.10,
         "max_drawdown": 3.50},
        {"name": "balanced-mid", "status": "ok", "trades_count": 195,
         "total_pnl": 19.0, "sharpe_ratio": 2.50, "profit_factor": 2.20,
         "max_drawdown": 4.20},
        {"name": "queue-clear-5m", "status": "ok", "trades_count": test_n,
         "total_pnl": 21.0, "sharpe_ratio": test_sharpe, "profit_factor": 2.90,
         "max_drawdown": 1.80,
         "sufficiency_rows": [
             {"key": "expectancy_usd", "label": "PnL Expectancy ($)",
              "type": "mean", "base": "closes", "base_n": test_n,
              "sufficient_95": False,
              "levels": [
                  {"confidence_pct": 95, "required_n": 1500,
                   "remaining_n": 1500 - test_n, "progress_pct": 12},
              ]},
         ]},
        {"name": "prudent-hybrid", "status": "ok", "trades_count": 190,
         "total_pnl": 17.0, "sharpe_ratio": 2.20, "profit_factor": 2.00,
         "max_drawdown": 5.00},
        {"name": "static-floor-2c", "status": "ok", "trades_count": 188,
         "total_pnl": 15.0, "sharpe_ratio": 2.00, "profit_factor": 1.90,
         "max_drawdown": 4.50},
    ]


def test_905_gate_map_names_the_rematch_candidate():
    arm_map = cockpit.DECISION_GATE_ARM_MAP["905"]
    assert arm_map["test_arm_pattern"] == "queue-clear-5m"
    assert arm_map["baseline_arm_names"] == ["loose-baseline"]


def test_905_provisional_pass_names_the_rank_lead():
    res = cockpit.compute_decision_gates(_healthy_905_arms(), "905")
    assert "PROVISIONAL PASS" in res["overall_verdict"]
    assert res["overall_badge"] == "badge-green"
    assert "rank lead" in res["overall_desc"]
    assert res["gates"]["starvation"]["status"] == "HEALTHY"


def test_905_starvation_names_the_rematch():
    res = cockpit.compute_decision_gates(_healthy_905_arms(test_n=90), "905")
    assert "VOLUME STARVATION" in res["overall_verdict"]
    assert res["overall_badge"] == "badge-red"
    assert "rematch" in res["overall_desc"]

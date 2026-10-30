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
        },
    ]
    res = cockpit.compute_decision_gates(arms, "843")
    assert "PROVISIONAL PASS" in res["overall_verdict"]
    assert res["overall_badge"] == "badge-green"
    assert res["gates"]["power"]["status"] == "UNDERPOWERED"
    assert res["gates"]["sharpe"]["status"] == "PASS"
    assert res["gates"]["pf"]["status"] == "PASS"
    assert res["gates"]["mdd"]["status"] == "PASS"
    assert res["gates"]["starvation"]["status"] == "HEALTHY"


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
            "trades_count": 2150,  # N >= 2,117
            "total_pnl": 180.0,
            "sharpe_ratio": 2.95,
            "profit_factor": 3.10,
            "max_drawdown": 2.20,
        },
    ]
    res = cockpit.compute_decision_gates(arms, "843")
    assert "EMPIRICAL WINNER" in res["overall_verdict"]
    assert res["overall_badge"] == "badge-green"
    assert res["gates"]["power"]["status"] == "PASS"


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
    assert '1. Sample Power Gate' in html
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
    assert data["decision_gates"]["target_n"] == 2117


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

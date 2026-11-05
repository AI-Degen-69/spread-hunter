"""Unit test suite for Issue #940 second-round tweak tournament."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core_brain.config import MakerConfig
from scripts.shadow_tournament import (
    RETIRED_HOLD_ENV,
    _RETIRED_PRESET_KEYS,
    build_tournament_plan,
)

ARMS_PATH = Path("configs/tournaments/i940-second-round-tweaks_arms.json")
pytestmark = pytest.mark.writes_real_state

EXPECTED_ARMS = [
    "loose-baseline",
    "tight-touch",
    "balanced-mid",
    "wide-shield",
    "prudent-hybrid",
    "static-floor-2c",
    "low-churn-deadband",
    "hair-trigger-2c",
    "patient-stop-9c",
    "queue-clear-5m",
]

EXPECTED_TWEAK_ENV = {
    "loose-baseline": {"BOT_ORDER_REQUOTE_DEAD_BAND_USD": "0.04"},
    "tight-touch": {"BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "3"},
    "balanced-mid": {"BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "5"},
    "wide-shield": {"BOT_RISK_STOP_ADVERSE_DROP_USD": "0.060"},
    "prudent-hybrid": {"BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "5"},
    "static-floor-2c": {"BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "6"},
    "low-churn-deadband": {"BOT_ORDER_REQUOTE_DEAD_BAND_USD": "0.04"},
    "hair-trigger-2c": {"BOT_RISK_STOP_ADVERSE_DROP_USD": "0.055"},
    "patient-stop-9c": {"BOT_RISK_STOP_ADVERSE_DROP_USD": "0.070"},
    "queue-clear-5m": {"BOT_ORDER_QUEUE_MAX_CLEAR_MIN": "2"},
}


def _load_arms() -> list[dict]:
    assert ARMS_PATH.is_file(), f"Arms file missing: {ARMS_PATH}"
    data = json.loads(ARMS_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    return data


def test_i940_arms_file_exists_and_schema():
    arms = _load_arms()
    assert len(arms) == 10, f"Expected 10 arms, got {len(arms)}"
    assert [a["name"] for a in arms] == EXPECTED_ARMS

    required_keys = {
        "name",
        "label",
        "color",
        "profile",
        "desc",
        "thesis",
        "worst_catalyst",
        "worst_catalyst_desc",
        "hypothesized_tweak",
        "tweak_rationale",
        "env",
    }
    for arm in arms:
        missing = required_keys - set(arm.keys())
        assert not missing, f"Arm {arm.get('name')} missing fields: {missing}"
        assert arm["color"].startswith("#")
        assert len(arm["name"]) <= 24
        assert isinstance(arm["env"], dict)
        assert arm["hypothesized_tweak"].startswith("BOT_"), arm["name"]
        assert arm["tweak_rationale"].startswith("Hypothesis:"), arm["name"]


def test_i940_cockpit_prose_matches_the_configured_tweaks():
    prose = {arm["name"]: arm["desc"] for arm in _load_arms()}

    assert "4c" in prose["loose-baseline"]
    assert "nothing changed" not in prose["loose-baseline"]
    assert "3c" in prose["tight-touch"]
    assert "tightest dynamic offset" not in prose["tight-touch"]
    assert "4c" in prose["low-churn-deadband"]
    assert "widest" not in prose["low-churn-deadband"].lower()
    assert "5.5c" in prose["hair-trigger-2c"]
    assert "7c" in prose["patient-stop-9c"]
    assert "two minutes" in prose["queue-clear-5m"]


def test_i940_arms_carry_the_concluded_tweaks():
    arms = {a["name"]: a for a in _load_arms()}
    for name, tweak in EXPECTED_TWEAK_ENV.items():
        for key, value in tweak.items():
            assert arms[name]["env"].get(key) == value, (
                f"Arm {name} must carry concluded tweak {key}={value}"
            )


def test_i940_all_arms_quote_live_sports_only(tmp_path: Path):
    arms = _load_arms()
    for arm in arms:
        assert arm["env"].get("BOT_MARKET_LIVE_SPORTS_ONLY_ON") == "1", (
            f"Arm {arm['name']} must quote live sports/esports only"
        )
        assert arm["env"].get("BOT_QUOTE_BANK_BASELINE_USD") == "100.0", (
            f"Arm {arm['name']} must reset to the $100 starting bankroll"
        )
    plan = build_tournament_plan(
        issue=940,
        arms=arms,
        minutes=1440.0,
        base_dir=tmp_path,
        stamp="2026-11-05T10-00",
        check_ports=False,
    )
    for arm_plan in plan.arms:
        assert arm_plan.env.get("BOT_MARKET_LIVE_SPORTS_ONLY_ON") == "1", (
            f"Plan arm {arm_plan.name} lost the live-sports lane switch"
        )


def test_i940_arms_no_retired_keys():
    arms = _load_arms()
    for arm in arms:
        env = arm["env"]
        retired_holds = RETIRED_HOLD_ENV & set(env.keys())
        assert not retired_holds, (
            f"Arm {arm['name']} sets retired hold keys: {retired_holds}"
        )
        retired_presets = _RETIRED_PRESET_KEYS & set(arm.keys())
        assert not retired_presets, (
            f"Arm {arm['name']} uses retired preset keys: {retired_presets}"
        )
        if "BOT_POS_PRICE_MAX_PAIR_COST_USD" in env:
            assert float(env["BOT_POS_PRICE_MAX_PAIR_COST_USD"]) <= 0.99


def test_i940_arms_effective_maker_config_resolution(monkeypatch):
    monkeypatch.setattr("core_brain.rehearsal.is_rehearsal", lambda: True)
    arms = {a["name"]: a["env"] for a in _load_arms()}

    def _load_for_arm(arm_env: dict[str, str]) -> MakerConfig:
        all_keys = {k for env in arms.values() for k in env}
        for k in all_keys:
            monkeypatch.delenv(k, raising=False)
        for k, v in arm_env.items():
            monkeypatch.setenv(k, v)
        return MakerConfig.from_env()

    assert _load_for_arm(arms["static-floor-2c"]).bot_quote_gate_dynamic_offset_on is True
    assert _load_for_arm(arms["static-floor-2c"]).bot_quote_spread_offset_max_cents == 6
    assert _load_for_arm(arms["low-churn-deadband"]).bot_quote_gate_dynamic_offset_on is False
    assert _load_for_arm(arms["low-churn-deadband"]).bot_order_requote_dead_band_usd == pytest.approx(0.04)
    assert _load_for_arm(arms["loose-baseline"]).bot_order_requote_dead_band_usd == pytest.approx(0.04)
    assert _load_for_arm(arms["loose-baseline"]).bot_quote_bank_baseline_usd == pytest.approx(100.0)
    assert _load_for_arm(arms["tight-touch"]).bot_quote_spread_offset_min_cents == 3
    assert _load_for_arm(arms["balanced-mid"]).bot_quote_spread_offset_max_cents == 5
    assert _load_for_arm(arms["prudent-hybrid"]).bot_quote_spread_offset_max_cents == 5
    assert _load_for_arm(arms["hair-trigger-2c"]).bot_risk_stop_adverse_drop_usd == pytest.approx(0.055)
    assert _load_for_arm(arms["patient-stop-9c"]).bot_risk_stop_adverse_drop_usd == pytest.approx(0.070)
    assert _load_for_arm(arms["wide-shield"]).bot_risk_stop_adverse_drop_usd == pytest.approx(0.060)
    assert _load_for_arm(arms["queue-clear-5m"]).bot_order_queue_max_clear_min == pytest.approx(2.0)


def test_i940_build_tournament_plan_and_dry_run_cli(tmp_path: Path):
    arms = _load_arms()
    stamp = "2026-11-05T10-00"
    plan = build_tournament_plan(
        issue=940,
        arms=arms,
        minutes=1440.0,
        base_dir=tmp_path,
        stamp=stamp,
        check_ports=False,
    )
    assert len(plan.arms) == 10
    assert [a.name for a in plan.arms] == EXPECTED_ARMS
    assert len({a.db_path for a in plan.arms}) == 10
    assert len({a.run_id for a in plan.arms}) == 10

    cmd = [
        sys.executable,
        "-m",
        "scripts.shadow_tournament",
        "--issue",
        "940",
        "--arms-file",
        str(ARMS_PATH),
        "--minutes",
        "1440",
        "--stamp",
        "second-round",
        "--dry-run",
        "--base-dir",
        str(tmp_path / "cli"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"CLI dry run failed: {proc.stderr}"
    plan_json = json.loads(proc.stdout)
    assert plan_json["issue"] == 940
    assert plan_json["minutes"] == 1440.0
    assert [a["name"] for a in plan_json["arms"]] == EXPECTED_ARMS
    assert "base_port" not in plan_json
    for arm_entry in plan_json["arms"]:
        assert "dash_port" not in arm_entry
        assert "dash_argv" not in arm_entry
        assert "--dash-port" not in arm_entry["shadow_argv"]


def test_i940_served_on_dashboard_cockpit_8799(monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard.server import app
    from scripts import tournament_cockpit_server as cockpit

    assert "940" in cockpit.SERVED_TOURNAMENT_KEYS
    assert [a["name"] for a in cockpit.ARMS_CONFIG_940] == EXPECTED_ARMS

    monkeypatch.setattr(cockpit, "_resolve_plan_for_tourney_key", lambda key, meta: None)
    res = TestClient(app).get("/cockpit/940")
    assert res.status_code == 200
    assert "Issue #940" in res.text
    assert 'href="/cockpit/940" class="tourney-pill active"' in res.text
    assert "http://127.0.0.1:8800" not in res.text
    for arm_name in EXPECTED_ARMS:
        assert arm_name in res.text

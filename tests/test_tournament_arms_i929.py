"""Unit test suite for Issue #929 6-arm momentum reversion tournament."""
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

ARMS_PATH = Path("configs/tournaments/i929-momentum-reversion_arms.json")
pytestmark = pytest.mark.writes_real_state

EXPECTED_ARMS = [
    "live-rider-2c-stop30pct",
    "live-rider-5c-stop20pct",
    "live-rider-8c-stop25pct",
    "live-rider-5c-stop15pct",
    "live-ud50-r5c-stop20pct",
    "live-ud50-r8c-stop25pct",
]

SAFETY_OVERRIDE_KEYS = frozenset({
    "BOT_RISK_STOP_ADVERSE_DROP_USD",
    "BOT_RISK_STOP_ADVERSE_DROP_CENTS",
    "BOT_ORDER_REQUOTE_DEAD_BAND_USD",
    "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS",
    "BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC",
})


def _load_arms() -> list[dict]:
    assert ARMS_PATH.is_file(), f"Arms file missing: {ARMS_PATH}"
    data = json.loads(ARMS_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    return data


def test_i929_arms_file_exists_and_schema():
    arms = _load_arms()
    assert len(arms) == 6, f"Expected 6 arms, got {len(arms)}"
    assert [a["name"] for a in arms] == EXPECTED_ARMS

    required_keys = {
        "name",
        "label",
        "color",
        "profile",
        "desc",
        "thesis",
        "hypothesis",
        "env",
    }
    for arm in arms:
        missing = required_keys - set(arm.keys())
        assert not missing, f"Arm {arm.get('name')} missing fields: {missing}"
        assert arm["color"].startswith("#")
        assert len(arm["name"]) <= 24
        assert arm["hypothesis"].strip()
        assert isinstance(arm["env"], dict)


def test_i929_arms_no_retired_keys():
    arms = _load_arms()
    for arm in arms:
        env = arm["env"]
        safety_found = SAFETY_OVERRIDE_KEYS & set(env.keys())
        assert not safety_found, (
            f"Arm {arm['name']} must not set disallowed safety overrides: {safety_found}"
        )
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


def test_i929_arms_effective_maker_config_resolution(monkeypatch):
    monkeypatch.setattr("core_brain.rehearsal.is_rehearsal", lambda: True)
    arms = {a["name"]: a["env"] for a in _load_arms()}

    def _load_for_arm(arm_env: dict[str, str]) -> MakerConfig:
        all_keys = {k for env in arms.values() for k in env}
        for k in all_keys:
            monkeypatch.delenv(k, raising=False)
        for k, v in arm_env.items():
            monkeypatch.setenv(k, v)
        return MakerConfig.from_env()

    # 1. live-rider-2c-stop30pct
    cfg_stop30 = _load_for_arm(arms["live-rider-2c-stop30pct"])
    assert arms["live-rider-2c-stop30pct"]["BOT_MARKET_LIVE_SPORTS_ONLY_ON"] == "1"
    assert cfg_stop30.bot_risk_stranded_max_wait_sec == pytest.approx(1800.0)
    assert cfg_stop30.bot_pos_price_max_entry_usd == pytest.approx(0.60)
    assert cfg_stop30.bot_risk_trailing_rider_on is True
    assert cfg_stop30.bot_risk_trailing_rider_delta_usd == pytest.approx(0.02)
    assert cfg_stop30.bot_risk_stop_mode == "PERCENTAGE_DROP"
    assert cfg_stop30.bot_risk_stop_max_drawdown_pct == pytest.approx(30.0)

    # 2. live-rider-5c-stop20pct
    cfg_r5c_s20 = _load_for_arm(arms["live-rider-5c-stop20pct"])
    assert arms["live-rider-5c-stop20pct"]["BOT_MARKET_LIVE_SPORTS_ONLY_ON"] == "1"
    assert cfg_r5c_s20.bot_pos_price_max_entry_usd == pytest.approx(0.60)
    assert cfg_r5c_s20.bot_risk_trailing_rider_on is True
    assert cfg_r5c_s20.bot_risk_trailing_rider_delta_usd == pytest.approx(0.05)
    assert cfg_r5c_s20.bot_risk_stop_mode == "PERCENTAGE_DROP"
    assert cfg_r5c_s20.bot_risk_stop_max_drawdown_pct == pytest.approx(20.0)

    # 3. live-rider-8c-stop25pct
    cfg_r8c_s25 = _load_for_arm(arms["live-rider-8c-stop25pct"])
    assert arms["live-rider-8c-stop25pct"]["BOT_MARKET_LIVE_SPORTS_ONLY_ON"] == "1"
    assert cfg_r8c_s25.bot_pos_price_max_entry_usd == pytest.approx(0.60)
    assert cfg_r8c_s25.bot_risk_trailing_rider_on is True
    assert cfg_r8c_s25.bot_risk_trailing_rider_delta_usd == pytest.approx(0.08)
    assert cfg_r8c_s25.bot_risk_stop_mode == "PERCENTAGE_DROP"
    assert cfg_r8c_s25.bot_risk_stop_max_drawdown_pct == pytest.approx(25.0)

    # 4. live-rider-5c-stop15pct
    cfg_r5c_s15 = _load_for_arm(arms["live-rider-5c-stop15pct"])
    assert arms["live-rider-5c-stop15pct"]["BOT_MARKET_LIVE_SPORTS_ONLY_ON"] == "1"
    assert cfg_r5c_s15.bot_pos_price_max_entry_usd == pytest.approx(0.60)
    assert cfg_r5c_s15.bot_risk_trailing_rider_on is True
    assert cfg_r5c_s15.bot_risk_trailing_rider_delta_usd == pytest.approx(0.05)
    assert cfg_r5c_s15.bot_risk_stop_mode == "PERCENTAGE_DROP"
    assert cfg_r5c_s15.bot_risk_stop_max_drawdown_pct == pytest.approx(15.0)

    # 5. live-ud50-r5c-stop20pct
    cfg_ud50_5c = _load_for_arm(arms["live-ud50-r5c-stop20pct"])
    assert arms["live-ud50-r5c-stop20pct"]["BOT_MARKET_LIVE_SPORTS_ONLY_ON"] == "1"
    assert cfg_ud50_5c.bot_pos_price_max_entry_usd == pytest.approx(0.50)
    assert cfg_ud50_5c.bot_risk_trailing_rider_on is True
    assert cfg_ud50_5c.bot_risk_trailing_rider_delta_usd == pytest.approx(0.05)
    assert cfg_ud50_5c.bot_risk_stop_mode == "PERCENTAGE_DROP"
    assert cfg_ud50_5c.bot_risk_stop_max_drawdown_pct == pytest.approx(20.0)
    assert cfg_ud50_5c.bot_quote_require_two_sided_when_flat_on is False

    # 6. live-ud50-r8c-stop25pct
    cfg_ud50_8c = _load_for_arm(arms["live-ud50-r8c-stop25pct"])
    assert arms["live-ud50-r8c-stop25pct"]["BOT_MARKET_LIVE_SPORTS_ONLY_ON"] == "1"
    assert cfg_ud50_8c.bot_pos_price_max_entry_usd == pytest.approx(0.50)
    assert cfg_ud50_8c.bot_risk_trailing_rider_on is True
    assert cfg_ud50_8c.bot_risk_trailing_rider_delta_usd == pytest.approx(0.08)
    assert cfg_ud50_8c.bot_risk_stop_mode == "PERCENTAGE_DROP"
    assert cfg_ud50_8c.bot_risk_stop_max_drawdown_pct == pytest.approx(25.0)
    assert cfg_ud50_8c.bot_quote_require_two_sided_when_flat_on is False

    # Verify standard arms retain strict two-sided requirement
    assert cfg_stop30.bot_quote_require_two_sided_when_flat_on is True


def test_i929_build_tournament_plan_and_dry_run_cli(tmp_path: Path):
    arms = _load_arms()
    stamp = "2026-11-03T20-00"
    plan = build_tournament_plan(
        issue=929,
        arms=arms,
        minutes=1440.0,
        base_dir=tmp_path,
        stamp=stamp,
        check_ports=False,
    )
    assert len(plan.arms) == 6
    assert [a.name for a in plan.arms] == EXPECTED_ARMS
    assert len({a.db_path for a in plan.arms}) == 6
    assert len({a.run_id for a in plan.arms}) == 6

    cmd = [
        sys.executable,
        "-m",
        "scripts.shadow_tournament",
        "--issue",
        "929",
        "--arms-file",
        str(ARMS_PATH),
        "--minutes",
        "1440",
        "--stamp",
        "momentum-reversion",
        "--dry-run",
        "--base-dir",
        str(tmp_path / "cli"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"CLI dry run failed: {proc.stderr}"
    plan_json = json.loads(proc.stdout)
    assert plan_json["issue"] == 929
    assert plan_json["minutes"] == 1440.0
    assert [a["name"] for a in plan_json["arms"]] == EXPECTED_ARMS
    assert "base_port" not in plan_json
    for arm_entry in plan_json["arms"]:
        assert "dash_port" not in arm_entry
        assert "dash_argv" not in arm_entry
        assert "--dash-port" not in arm_entry["shadow_argv"]


def test_i929_served_on_dashboard_cockpit_8799(monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard.server import app
    from scripts import tournament_cockpit_server as cockpit

    assert "929" in cockpit.SERVED_TOURNAMENT_KEYS
    assert [a["name"] for a in cockpit.ARMS_CONFIG_929] == EXPECTED_ARMS

    monkeypatch.setattr(cockpit, "_resolve_plan_for_tourney_key", lambda key, meta: None)
    res = TestClient(app).get("/cockpit/929")
    assert res.status_code == 200
    assert "Issue #929" in res.text
    assert 'href="/cockpit/929" class="tourney-pill active"' in res.text
    assert "http://127.0.0.1:8800" not in res.text
    for arm_name in EXPECTED_ARMS:
        assert arm_name in res.text

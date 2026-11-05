"""Unit test suite for Issue #938 10-arm tweak-rematch tournament."""
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

ARMS_PATH = Path("configs/tournaments/i938-tweak-rematch_arms.json")
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
    "loose-baseline": {"BOT_ORDER_REQUOTE_DEAD_BAND_USD": "0.06"},
    "tight-touch": {"BOT_QUOTE_TOURNAMENT_PRESET": "balanced"},
    "balanced-mid": {"BOT_ORDER_REQUOTE_DEAD_BAND_USD": "0.06"},
    "wide-shield": {"BOT_RISK_STOP_ADVERSE_DROP_USD": "0.050"},
    "prudent-hybrid": {"BOT_ORDER_REQUOTE_DEAD_BAND_USD": "0.06"},
    "static-floor-2c": {"BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1"},
    "low-churn-deadband": {"BOT_ORDER_REQUOTE_DEAD_BAND_USD": "0.06"},
    "hair-trigger-2c": {"BOT_RISK_STOP_ADVERSE_DROP_USD": "0.045"},
    "patient-stop-9c": {"BOT_RISK_STOP_ADVERSE_DROP_USD": "0.060"},
    "queue-clear-5m": {"BOT_ORDER_QUEUE_MAX_CLEAR_MIN": "3"},
}


def _load_arms() -> list[dict]:
    assert ARMS_PATH.is_file(), f"Arms file missing: {ARMS_PATH}"
    data = json.loads(ARMS_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    return data


def test_i938_arms_file_exists_and_schema():
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


def test_i938_arms_carry_the_concluded_tweaks():
    arms = {a["name"]: a for a in _load_arms()}
    for name, tweak in EXPECTED_TWEAK_ENV.items():
        for key, value in tweak.items():
            assert arms[name]["env"].get(key) == value, (
                f"Arm {name} must carry concluded tweak {key}={value}"
            )


def test_i938_arms_no_retired_keys():
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


def test_i938_arms_effective_maker_config_resolution(monkeypatch):
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
    assert _load_for_arm(arms["low-churn-deadband"]).bot_quote_gate_dynamic_offset_on is False
    assert _load_for_arm(arms["low-churn-deadband"]).bot_order_requote_dead_band_usd == pytest.approx(0.06)
    assert _load_for_arm(arms["hair-trigger-2c"]).bot_risk_stop_adverse_drop_usd == pytest.approx(0.045)
    assert _load_for_arm(arms["patient-stop-9c"]).bot_risk_stop_adverse_drop_usd == pytest.approx(0.060)
    assert _load_for_arm(arms["wide-shield"]).bot_risk_stop_adverse_drop_usd == pytest.approx(0.050)
    assert _load_for_arm(arms["queue-clear-5m"]).bot_order_queue_max_clear_min == pytest.approx(3.0)


def test_i938_build_tournament_plan_and_dry_run_cli(tmp_path: Path):
    arms = _load_arms()
    stamp = "2026-11-05T10-00"
    plan = build_tournament_plan(
        issue=938,
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
        "938",
        "--arms-file",
        str(ARMS_PATH),
        "--minutes",
        "1440",
        "--stamp",
        "tweak-rematch",
        "--dry-run",
        "--base-dir",
        str(tmp_path / "cli"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"CLI dry run failed: {proc.stderr}"
    plan_json = json.loads(proc.stdout)
    assert plan_json["issue"] == 938
    assert plan_json["minutes"] == 1440.0
    assert [a["name"] for a in plan_json["arms"]] == EXPECTED_ARMS
    assert "base_port" not in plan_json
    for arm_entry in plan_json["arms"]:
        assert "dash_port" not in arm_entry
        assert "dash_argv" not in arm_entry
        assert "--dash-port" not in arm_entry["shadow_argv"]


def test_i938_served_on_dashboard_cockpit_8799(monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard.server import app
    from scripts import tournament_cockpit_server as cockpit

    assert "938" in cockpit.SERVED_TOURNAMENT_KEYS
    assert [a["name"] for a in cockpit.ARMS_CONFIG_938] == EXPECTED_ARMS

    monkeypatch.setattr(cockpit, "_resolve_plan_for_tourney_key", lambda key, meta: None)
    res = TestClient(app).get("/cockpit/938")
    assert res.status_code == 200
    assert "Issue #938" in res.text
    assert 'href="/cockpit/938" class="tourney-pill active"' in res.text
    assert "http://127.0.0.1:8800" not in res.text
    for arm_name in EXPECTED_ARMS:
        assert arm_name in res.text

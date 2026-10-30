"""Unit test suite for Issue #843 4-arm tournament configuration and plan resolution."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.shadow_tournament import (
    RETIRED_HOLD_ENV,
    _RETIRED_PRESET_KEYS,
    build_tournament_plan,
)

ARMS_PATH = Path("configs/tournaments/i843-pair-test_arms.json")

# Both spellings of each money override: #872 renamed the keys, and the retired
# `_CENTS` names still load through the deprecation window, so an arm setting
# one is just as effective a safety override as the `_USD` name.
SAFETY_OVERRIDE_KEYS = frozenset({
    "BOT_RISK_STOP_MODE",
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


def test_i843_arms_file_exists_and_schema():
    arms = _load_arms()
    assert len(arms) == 4, f"Expected 4 arms, got {len(arms)}"
    expected_names = ["control", "balanced-only", "queue5-only", "balanced-queue5"]
    assert [a["name"] for a in arms] == expected_names

    required_keys = {"name", "label", "color", "profile", "desc", "thesis", "env"}
    for arm in arms:
        missing = required_keys - set(arm.keys())
        assert not missing, f"Arm {arm.get('name')} missing fields: {missing}"
        assert arm["color"].startswith("#")
        assert len(arm["name"]) <= 24


def test_i843_arms_environment_variables():
    arms = {a["name"]: a for a in _load_arms()}

    assert arms["control"]["env"] == {}

    assert arms["balanced-only"]["env"] == {
        "BOT_QUOTE_TOURNAMENT_PRESET": "balanced",
    }

    assert arms["queue5-only"]["env"] == {
        "BOT_ORDER_GATE_QUEUE_CLEAR_ON": "1",
        "BOT_ORDER_QUEUE_MAX_CLEAR_MIN": "5",
    }

    assert arms["balanced-queue5"]["env"] == {
        "BOT_QUOTE_TOURNAMENT_PRESET": "balanced",
        "BOT_ORDER_GATE_QUEUE_CLEAR_ON": "1",
        "BOT_ORDER_QUEUE_MAX_CLEAR_MIN": "5",
    }


def test_i843_arms_no_safety_or_retired_keys():
    arms = _load_arms()
    for arm in arms:
        env = arm["env"]
        # No safety overrides
        safety_found = SAFETY_OVERRIDE_KEYS & set(env.keys())
        assert not safety_found, (
            f"Arm {arm['name']} must not set safety overrides: {safety_found}"
        )
        # No retired hold keys
        retired_holds = RETIRED_HOLD_ENV & set(env.keys())
        assert not retired_holds, (
            f"Arm {arm['name']} sets retired hold keys: {retired_holds}"
        )
        # No retired preset keys in arm dict
        retired_presets = _RETIRED_PRESET_KEYS & set(arm.keys())
        assert not retired_presets, (
            f"Arm {arm['name']} uses retired preset keys: {retired_presets}"
        )


def test_i843_build_tournament_plan_resolution(tmp_path: Path):
    arms = _load_arms()
    stamp = "2026-10-26T21-00"
    plan = build_tournament_plan(
        issue=843,
        arms=arms,
        base_dir=tmp_path,
        stamp=stamp,
        check_ports=False,
    )

    assert len(plan.arms) == 4
    arm_names = [a.name for a in plan.arms]
    assert arm_names == ["control", "balanced-only", "queue5-only", "balanced-queue5"]

    # Each arm gets isolated database and distinct run_id
    db_paths = [a.db_path for a in plan.arms]
    assert len(set(db_paths)) == 4
    for p in db_paths:
        assert p.parent.name == f"i843_{stamp}"

    run_ids = [a.run_id for a in plan.arms]
    assert len(set(run_ids)) == 4

    # Combined arm carries exactly the intended keys
    bq_env = plan.arms[3].env
    assert bq_env["BOT_QUOTE_TOURNAMENT_PRESET"] == "balanced"
    assert bq_env["BOT_ORDER_GATE_QUEUE_CLEAR_ON"] == "1"
    assert bq_env["BOT_ORDER_QUEUE_MAX_CLEAR_MIN"] == "5"
    assert not (SAFETY_OVERRIDE_KEYS & set(bq_env.keys()))


def test_i843_dry_run_cli(tmp_path: Path):
    cmd = [
        sys.executable,
        "-m",
        "scripts.shadow_tournament",
        "--issue",
        "843",
        "--arms-file",
        str(ARMS_PATH),
        "--dry-run",
        "--base-dir",
        str(tmp_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"CLI dry run failed: {proc.stderr}"

    plan_json = json.loads(proc.stdout)
    assert plan_json["issue"] == 843
    assert len(plan_json["arms"]) == 4
    assert [a["name"] for a in plan_json["arms"]] == [
        "control",
        "balanced-only",
        "queue5-only",
        "balanced-queue5",
    ]

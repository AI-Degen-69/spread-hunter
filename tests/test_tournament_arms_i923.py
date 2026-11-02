"""Unit test suite for Issue #923 live-sports duel tournament.

Two arms over the market-regime split, each setting exactly one new lane
switch: live-sports-only (BOT_MARKET_LIVE_SPORTS_ONLY_ON) vs
all-but-live-sports (BOT_MARKET_EXCLUDE_LIVE_SPORTS_ON).
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from core_brain.market_feed import GraduatedMarket, GraduatedSnapshot
from core_brain.trader_loop import _market_specs
from scripts.shadow_tournament import (
    RETIRED_HOLD_ENV,
    _RETIRED_PRESET_KEYS,
    build_tournament_plan,
)

ARMS_PATH = Path("configs/tournaments/i923-live-sports-duel_arms.json")

EXPECTED_ARMS = [
    "live-sports-only",
    "all-but-live-sports",
]

SAFETY_OVERRIDE_KEYS = frozenset({
    "BOT_RISK_STOP_MODE",
    "BOT_RISK_STOP_ADVERSE_DROP_USD",
    "BOT_RISK_STOP_ADVERSE_DROP_CENTS",
    "BOT_ORDER_REQUOTE_DEAD_BAND_USD",
    "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS",
    "BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC",
})

NEW_923_SWITCHES = frozenset({
    "BOT_MARKET_LIVE_SPORTS_ONLY_ON",
    "BOT_MARKET_EXCLUDE_LIVE_SPORTS_ON",
})


def _load_arms() -> list[dict]:
    assert ARMS_PATH.is_file(), f"Arms file missing: {ARMS_PATH}"
    data = json.loads(ARMS_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    return data


def _sample_graduated_market(
    cid: str,
    title: str,
    slug: str,
    *,
    event_live: bool = False,
    sports_market_type: str = "",
    event_title: str = "",
) -> GraduatedMarket:
    return GraduatedMarket(
        cid=cid,
        min_size=5.0,
        tick=0.01,
        max_spread=0.04,
        days_to_resolve=5.0,
        source="spread",
        daily=0.0,
        shares=20,
        title=title,
        slug=slug,
        sports_market_type=sports_market_type,
        event_score="",
        event_period="",
        question=title,
        event_live=event_live,
        event_ended=False,
        series_ts=None,
        range_cents=4.0,
        velocity_measured_at=1700000000.0,
        event_title=event_title or title,
    )


def _923_snapshot() -> GraduatedSnapshot:
    """Five-row universe: macro, prematch sports, live sports, live
    political, and an ended live sports market."""
    macro_gm = _sample_graduated_market(
        "0xmacro1",
        "Will the Fed cut interest rates in December?",
        "fed-rate-cut-december",
        event_live=False,
    )
    prematch_sports_gm = _sample_graduated_market(
        "0xsports1",
        "Lakers vs Celtics Winner",
        "nba-lal-bos-2026-11-01",
        event_live=False,
        sports_market_type="moneyline",
    )
    live_sports_gm = _sample_graduated_market(
        "0xlivesports",
        "Chiefs vs 49ers Winner",
        "nfl-kc-sf-2026-11-01",
        event_live=True,
        sports_market_type="moneyline",
    )
    live_political_gm = replace(
        macro_gm,
        cid="0xlivepol",
        title="Live Political Debate Mention",
        slug="debate-mention",
        event_live=True,
    )
    ended_live_sports_gm = replace(
        live_sports_gm,
        cid="0xendedlivesports",
        event_ended=True,
    )
    return GraduatedSnapshot(
        rows=[macro_gm, prematch_sports_gm, live_sports_gm,
              live_political_gm, ended_live_sports_gm],
        published_mtime_ns=923923923,
        condition_ids=frozenset({
            "0xmacro1", "0xsports1", "0xlivesports",
            "0xlivepol", "0xendedlivesports",
        }),
    )


def _923_clear_lane_switches(monkeypatch):
    for var in ("BOT_MARKET_LIVE_ONLY_ON", "BOT_MARKET_EXCLUDE_LIVE_ON",
                "BOT_MARKET_EXCLUDE_SPORTS_ON", "FILTER_EXCLUDE_SPORTS",
                "BOT_MARKET_LIVE_SPORTS_ONLY_ON",
                "BOT_MARKET_EXCLUDE_LIVE_SPORTS_ON"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "core_brain.market_feed.load_graduated_snapshot",
        lambda path=None: _923_snapshot(),
    )


def test_923_live_sports_only_keeps_only_in_play_sports(monkeypatch):
    _923_clear_lane_switches(monkeypatch)
    monkeypatch.setenv("BOT_MARKET_LIVE_SPORTS_ONLY_ON", "1")
    assert [s["cid"] for s in _market_specs()] == ["0xlivesports"]


def test_923_exclude_live_sports_keeps_everything_else(monkeypatch):
    _923_clear_lane_switches(monkeypatch)
    monkeypatch.setenv("BOT_MARKET_EXCLUDE_LIVE_SPORTS_ON", "1")
    assert [s["cid"] for s in _market_specs()] == [
        "0xmacro1", "0xsports1", "0xlivepol", "0xendedlivesports",
    ]


def test_923_duel_arms_partition_the_universe(monkeypatch):
    _923_clear_lane_switches(monkeypatch)
    monkeypatch.setenv("BOT_MARKET_LIVE_SPORTS_ONLY_ON", "1")
    only = {s["cid"] for s in _market_specs()}
    monkeypatch.setenv("BOT_MARKET_LIVE_SPORTS_ONLY_ON", "0")
    monkeypatch.setenv("BOT_MARKET_EXCLUDE_LIVE_SPORTS_ON", "on")
    rest = {s["cid"] for s in _market_specs()}
    assert only == {"0xlivesports"}
    assert only & rest == set()
    assert only | rest == {
        "0xmacro1", "0xsports1", "0xlivesports",
        "0xlivepol", "0xendedlivesports",
    }


def test_923_conflicting_duel_switches_select_nothing(monkeypatch):
    _923_clear_lane_switches(monkeypatch)
    monkeypatch.setenv("BOT_MARKET_LIVE_SPORTS_ONLY_ON", "1")
    monkeypatch.setenv("BOT_MARKET_EXCLUDE_LIVE_SPORTS_ON", "1")
    assert _market_specs() == []


def test_923_exclude_live_sports_combined_with_exclude_sports(monkeypatch):
    # Exclude-sports already removes every live market, so with both switches
    # the intersection equals exclude-sports alone: the macro book only.
    _923_clear_lane_switches(monkeypatch)
    monkeypatch.setenv("BOT_MARKET_EXCLUDE_SPORTS_ON", "1")
    monkeypatch.setenv("BOT_MARKET_EXCLUDE_LIVE_SPORTS_ON", "1")
    assert [s["cid"] for s in _market_specs()] == ["0xmacro1"]


def test_i923_arms_file_exists_and_schema():
    arms = _load_arms()
    assert len(arms) == 2, f"Expected 2 arms, got {len(arms)}"
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


def test_i923_arms_use_exactly_one_new_switch_each():
    arms = _load_arms()
    for arm in arms:
        env = arm["env"]
        new_found = NEW_923_SWITCHES & set(env.keys())
        assert new_found, f"Arm {arm['name']} sets no #923 lane switch"
        assert len(env) == 1, (
            f"Arm {arm['name']} must isolate one knob, sets {sorted(env)}"
        )
        assert env[next(iter(new_found))] == "1"


def test_i923_arms_no_safety_or_retired_keys():
    arms = _load_arms()
    for arm in arms:
        env = arm["env"]
        safety_found = SAFETY_OVERRIDE_KEYS & set(env.keys())
        assert not safety_found, (
            f"Arm {arm['name']} must not set safety overrides: {safety_found}"
        )
        retired_holds = RETIRED_HOLD_ENV & set(env.keys())
        assert not retired_holds, (
            f"Arm {arm['name']} sets retired hold keys: {retired_holds}"
        )
        retired_presets = _RETIRED_PRESET_KEYS & set(arm.keys())
        assert not retired_presets, (
            f"Arm {arm['name']} uses retired preset keys: {retired_presets}"
        )


def test_i923_build_tournament_plan_and_dry_run_cli(tmp_path: Path):
    arms = _load_arms()
    stamp = "2026-11-02T20-00"
    plan = build_tournament_plan(
        issue=923,
        arms=arms,
        minutes=1440.0,
        base_dir=tmp_path,
        stamp=stamp,
        check_ports=False,
    )
    assert len(plan.arms) == 2
    assert [a.name for a in plan.arms] == EXPECTED_ARMS
    assert len({a.db_path for a in plan.arms}) == 2
    assert len({a.run_id for a in plan.arms}) == 2

    cmd = [
        sys.executable,
        "-m",
        "scripts.shadow_tournament",
        "--issue",
        "923",
        "--arms-file",
        str(ARMS_PATH),
        "--minutes",
        "1440",
        "--stamp",
        "live-sports-duel",
        "--dry-run",
        "--base-dir",
        str(tmp_path / "cli"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"CLI dry run failed: {proc.stderr}"
    plan_json = json.loads(proc.stdout)
    assert plan_json["issue"] == 923
    assert plan_json["minutes"] == 1440.0
    assert [a["name"] for a in plan_json["arms"]] == EXPECTED_ARMS
    assert "base_port" not in plan_json
    for arm_entry in plan_json["arms"]:
        assert "dash_port" not in arm_entry
        assert "dash_argv" not in arm_entry
        assert "--dash-port" not in arm_entry["shadow_argv"]


def test_i923_served_on_dashboard_cockpit_8799():
    from fastapi.testclient import TestClient
    from dashboard.server import app
    from scripts import tournament_cockpit_server as cockpit

    assert "923" in cockpit.SERVED_TOURNAMENT_KEYS
    assert [a["name"] for a in cockpit.ARMS_CONFIG_923] == EXPECTED_ARMS

    res = TestClient(app).get("/cockpit/923")
    assert res.status_code == 200
    assert "Issue #923" in res.text
    assert 'href="/cockpit/923" class="tourney-pill active"' in res.text
    assert "http://127.0.0.1:8800" not in res.text
    for arm_name in EXPECTED_ARMS:
        assert arm_name in res.text

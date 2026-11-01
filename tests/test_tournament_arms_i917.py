"""Unit test suite for Issue #917 multi-strategy adverse-selection defense tournament."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from core_brain.config import MakerConfig
from core_brain.market_feed import GraduatedMarket, GraduatedSnapshot
from core_brain.trader_loop import _market_specs
from scripts.shadow_tournament import (
    RETIRED_HOLD_ENV,
    _RETIRED_PRESET_KEYS,
    build_tournament_plan,
)

ARMS_PATH = Path("configs/tournaments/i917-multistrat-defense_arms.json")

EXPECTED_ARMS = [
    "macro-non-sports",
    "taker-snapper",
    "fast-bailout-60s",
    "wide-cushion-live",
    "queue-gated-hybrid",
    "control-baseline",
]

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


def test_from_env_parses_stranded_completion_and_max_wait(monkeypatch):
    monkeypatch.setenv("BOT_RISK_STRANDED_COMPLETION_ON", "1")
    monkeypatch.setenv("BOT_RISK_STRANDED_MAX_WAIT_SEC", "10.0")
    cfg = MakerConfig.from_env()
    assert cfg.bot_risk_stranded_completion_on is True
    assert cfg.bot_risk_stranded_max_wait_sec == pytest.approx(10.0)

    monkeypatch.setenv("BOT_RISK_STRANDED_COMPLETION_ON", "0")
    monkeypatch.setenv("BOT_RISK_STRANDED_MAX_WAIT_SEC", "45.0")
    cfg2 = MakerConfig.from_env()
    assert cfg2.bot_risk_stranded_completion_on is False
    assert cfg2.bot_risk_stranded_max_wait_sec == pytest.approx(45.0)


def test_from_env_parses_pairs_exit_window_and_hold_max_alias(monkeypatch):
    monkeypatch.setenv("BOT_RISK_PAIRS_EXIT_WINDOW_SEC", "60.0")
    cfg = MakerConfig.from_env()
    assert cfg.bot_risk_pairs_exit_window_sec == pytest.approx(60.0)

    monkeypatch.delenv("BOT_RISK_PAIRS_EXIT_WINDOW_SEC", raising=False)
    monkeypatch.setenv("BOT_POS_HOLD_MAX_SEC", "90.0")
    cfg2 = MakerConfig.from_env()
    assert cfg2.bot_risk_pairs_exit_window_sec == pytest.approx(90.0)


def test_from_env_parses_book_max_spread_usd_and_widens_window(monkeypatch):
    monkeypatch.delenv("BOT_QUOTE_WIDE_BOOK_TRIAL", raising=False)
    monkeypatch.setenv("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", "8")  # 0.08
    monkeypatch.setenv("BOT_QUOTE_BOOK_MAX_SPREAD_USD", "0.12")
    cfg = MakerConfig.from_env()
    assert cfg.bot_quote_book_max_spread_usd == pytest.approx(0.12)
    assert cfg.bot_quote_book_max_spread_from_mid == pytest.approx(0.12)


def test_market_specs_excludes_sports_and_live_when_env_set(monkeypatch):
    macro_gm = _sample_graduated_market(
        "0xmacro1",
        "Will the Fed cut interest rates in December?",
        "fed-rate-cut-december",
        event_live=False,
    )
    sports_type_gm = _sample_graduated_market(
        "0xsports1",
        "Lakers vs Celtics Winner",
        "nba-lal-bos-2026-11-01",
        event_live=False,
        sports_market_type="moneyline",
    )
    live_event_gm = replace(
        macro_gm,
        cid="0xlive1",
        title="Live Political Debate Mention",
        slug="debate-mention",
        event_live=True,
    )
    snapshot = GraduatedSnapshot(
        rows=[macro_gm, sports_type_gm, live_event_gm],
        published_mtime_ns=123456789,
        condition_ids=frozenset({"0xmacro1", "0xsports1", "0xlive1"}),
    )
    monkeypatch.setattr(
        "core_brain.market_feed.load_graduated_snapshot",
        lambda path=None: snapshot,
    )

    monkeypatch.delenv("BOT_MARKET_EXCLUDE_SPORTS_ON", raising=False)
    monkeypatch.delenv("FILTER_EXCLUDE_SPORTS", raising=False)
    all_specs = _market_specs()
    assert [s["cid"] for s in all_specs] == ["0xmacro1", "0xsports1", "0xlive1"]

    monkeypatch.setenv("BOT_MARKET_EXCLUDE_SPORTS_ON", "1")
    filtered_specs = _market_specs()
    assert [s["cid"] for s in filtered_specs] == ["0xmacro1"]


def test_i917_arms_file_exists_and_schema():
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


def test_i917_arms_no_safety_or_retired_keys():
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
        if "BOT_POS_PRICE_MAX_PAIR_COST_USD" in env:
            assert float(env["BOT_POS_PRICE_MAX_PAIR_COST_USD"]) <= 0.99


def test_i917_arms_effective_maker_config_resolution(monkeypatch):
    monkeypatch.setattr("core_brain.rehearsal.is_rehearsal", lambda: True)
    arms = {a["name"]: a["env"] for a in _load_arms()}

    def _load_for_arm(arm_env: dict[str, str]) -> MakerConfig:
        for k in list(
            {k for env in arms.values() for k in env}
            | {"BOT_QUOTE_WIDE_BOOK_TRIAL"}
        ):
            monkeypatch.delenv(k, raising=False)
        for k, v in arm_env.items():
            monkeypatch.setenv(k, v)
        return MakerConfig.from_env()

    macro_cfg = _load_for_arm(arms["macro-non-sports"])
    assert arms["macro-non-sports"]["BOT_MARKET_EXCLUDE_SPORTS_ON"] == "1"
    assert macro_cfg.bot_order_gate_queue_clear_on is True
    assert macro_cfg.bot_order_queue_max_clear_min == pytest.approx(5.0)

    snap_cfg = _load_for_arm(arms["taker-snapper"])
    assert snap_cfg.bot_risk_stranded_completion_on is True
    assert snap_cfg.bot_risk_stranded_max_wait_sec == pytest.approx(10.0)
    assert snap_cfg.bot_pos_price_max_pair_cost_usd == pytest.approx(0.99)

    bail_cfg = _load_for_arm(arms["fast-bailout-60s"])
    assert bail_cfg.bot_risk_pairs_exit_window_sec == pytest.approx(60.0)
    assert bail_cfg.bot_risk_stranded_max_wait_sec == pytest.approx(45.0)

    wide_cfg = _load_for_arm(arms["wide-cushion-live"])
    assert wide_cfg.bot_pos_price_max_pair_cost_usd == pytest.approx(0.96)
    assert wide_cfg.bot_quote_book_max_spread_usd == pytest.approx(0.12)
    assert wide_cfg.bot_quote_book_max_spread_from_mid >= 0.12

    hybrid_cfg = _load_for_arm(arms["queue-gated-hybrid"])
    assert hybrid_cfg.bot_order_gate_queue_clear_on is True
    assert hybrid_cfg.bot_order_queue_max_clear_min == pytest.approx(8.0)
    assert hybrid_cfg.bot_risk_pairs_exit_window_sec == pytest.approx(90.0)
    assert hybrid_cfg.bot_quote_spread_offset_max_age_sec == pytest.approx(30.0)

    ctrl_cfg = _load_for_arm(arms["control-baseline"])
    assert ctrl_cfg.bot_order_gate_queue_clear_on is True
    assert ctrl_cfg.bot_order_queue_max_clear_min == pytest.approx(5.0)


def test_i917_build_tournament_plan_and_dry_run_cli(tmp_path: Path):
    arms = _load_arms()
    stamp = "2026-11-01T20-00"
    plan = build_tournament_plan(
        issue=917,
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
        "917",
        "--arms-file",
        str(ARMS_PATH),
        "--minutes",
        "1440",
        "--stamp",
        "multistrat-defense",
        "--dry-run",
        "--base-dir",
        str(tmp_path / "cli"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"CLI dry run failed: {proc.stderr}"
    plan_json = json.loads(proc.stdout)
    assert plan_json["issue"] == 917
    assert plan_json["minutes"] == 1440.0
    assert [a["name"] for a in plan_json["arms"]] == EXPECTED_ARMS

"""Focused tests for dashboard/arm_params.py — issue #866, task T1.

Dashboard-only, money-free: the child evaluation runs the real config loaders
with a worker-style env and `declare_rehearsal()`; it never trades and never
touches the venue. Fixture plans live under tmp_path, never in data/.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dashboard import arm_params
from dashboard.arm_params import ArmParamsError


def _write_plan(root: Path, dirname: str, *, issue: int, arms: list[dict],
                run_number: int | None = 1, stamp: str = "2026-10-30T12-00") -> Path:
    # Real run-folder shape: i<issue>-r<run>_<stamp> (see
    # core_brain.shadow_run.TOURNAMENT_RUN_DIR_PATTERN).
    key = f"i{issue}-r{run_number}"
    d = root / f"{key}_{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    plan = {"issue": issue, "stamp": stamp, "run_number": run_number,
            "minutes": 60.0, "arms": arms}
    (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return d / "plan.json"


@pytest.fixture
def tourn_root(tmp_path):
    root = tmp_path / "tournaments"
    root.mkdir()
    return root


@pytest.fixture(autouse=True)
def _clean_memo():
    arm_params.clear_memo()
    yield
    arm_params.clear_memo()


def test_empty_root_has_no_plans(tourn_root):
    assert arm_params.list_plan_catalog(tourn_root) == []


def test_catalog_picks_newest_plan_per_issue(tourn_root):
    _write_plan(tourn_root, "", issue=866,
                arms=[{"name": "control", "env": {}}],
                run_number=1, stamp="2026-10-29T12-00")
    new_plan = _write_plan(tourn_root, "", issue=866,
                           arms=[{"name": "control", "env": {}},
                                 {"name": "balanced-only",
                                  "env": {"BOT_QUOTE_TOURNAMENT_PRESET": "balanced"}}],
                           run_number=2, stamp="2026-10-30T12-00")
    catalog = arm_params.list_plan_catalog(tourn_root)
    assert len(catalog) == 1
    assert catalog[0]["plan_key"] == new_plan.parent.name
    assert catalog[0]["issue"] == 866
    assert catalog[0]["arms"] == ["control", "balanced-only"]


def test_resolve_prefers_explicit_plan(tourn_root):
    _write_plan(tourn_root, "", issue=866,
                arms=[{"name": "control", "env": {}}],
                run_number=1, stamp="2026-10-29T12-00")
    explicit = _write_plan(tourn_root, "", issue=866,
                            arms=[{"name": "control", "env": {}}],
                            run_number=2, stamp="2026-10-30T12-00")
    got = arm_params.resolve_plan_path(plan=explicit.parent.name,
                                       tourn_root=tourn_root)
    assert got == explicit


def test_resolve_tourney_picks_newest_plan(tourn_root):
    _write_plan(tourn_root, "", issue=866,
                arms=[{"name": "control", "env": {}}], run_number=1)
    newest = _write_plan(tourn_root, "", issue=866,
                         arms=[{"name": "control", "env": {}}], run_number=2)
    assert arm_params.resolve_plan_path(tourney=866, tourn_root=tourn_root) == newest
    assert arm_params.resolve_plan_path(tourney="866 ", tourn_root=tourn_root) == newest


def test_resolve_malformed_tourney_raises_no_plan(tourn_root):
    with pytest.raises(ArmParamsError) as exc:
        arm_params.resolve_plan_path(tourney="not-a-number", tourn_root=tourn_root)
    assert exc.value.code == "NO_PLAN"


def test_resolve_plan_traversal_raises_no_plan(tourn_root):
    with pytest.raises(ArmParamsError) as exc:
        arm_params.resolve_plan_path(plan="../../outside", tourn_root=tourn_root)
    assert exc.value.code == "NO_PLAN"


def test_resolve_unknown_plan_raises_no_plan(tourn_root):
    with pytest.raises(ArmParamsError) as exc:
        arm_params.resolve_plan_path(plan="i866_r9_missing_20261030-120000",
                                     tourn_root=tourn_root)
    assert exc.value.code == "NO_PLAN"


def test_resolve_unknown_arm_raises(tourn_root):
    plan = _write_plan(tourn_root, "", issue=866,
                       arms=[{"name": "control", "env": {}}])
    with pytest.raises(ArmParamsError) as exc:
        arm_params.resolve_arm(plan, "no-such-arm")
    assert exc.value.code == "UNKNOWN_ARM"


def test_exact_arm_match_wins_over_suffix_candidate(tourn_root):
    plan = _write_plan(tourn_root, "", issue=866,
                       arms=[{"name": "queue5-only", "env": {}},
                             {"name": "queue5-only-2", "env": {}}])
    arm = arm_params.resolve_arm(plan, "queue5-only-2")
    assert arm["name"] == "queue5-only-2"


def test_ambiguous_suffix_match_raises(tourn_root):
    plan = _write_plan(tourn_root, "", issue=866,
                       arms=[{"name": "balanced-2", "env": {}},
                             {"name": "balanced-3", "env": {}}])
    with pytest.raises(ArmParamsError) as exc:
        arm_params.resolve_arm(plan, "balanced")
    assert exc.value.code == "AMBIGUOUS_ARM"


def test_malformed_plan_raises(tourn_root):
    d = tourn_root / "i866-r1_2026-10-30T12-00"
    d.mkdir()
    bad = d / "plan.json"
    bad.write_text(json.dumps({"arms": [{"env": {}}]}), encoding="utf-8")
    with pytest.raises(ArmParamsError) as exc:
        arm_params.read_record_arms(bad)
    assert exc.value.code == "MALFORMED_PLAN"


def test_child_env_strips_parent_bot_vars_and_applies_arm(tourn_root, monkeypatch):
    monkeypatch.setenv("BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS", "99")
    monkeypatch.setenv("FILTER_LIVE_VOL_MIN_24H_USD", "1")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    plan = _write_plan(tourn_root, "", issue=866,
                       arms=[{"name": "balanced-only",
                              "env": {"BOT_QUOTE_TOURNAMENT_PRESET": "balanced"},
                              "db_path": "data/arm.db"}])
    arm = arm_params.resolve_arm(plan, "balanced-only")
    env = arm_params.build_child_env(arm)
    assert env["BOT_QUOTE_TOURNAMENT_PRESET"] == "balanced"
    assert env["SPREAD_HUNTER_DB"] == "data/arm.db"
    assert "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS" not in env
    assert "FILTER_LIVE_VOL_MIN_24H_USD" not in env
    assert "PATH" in env


def test_child_evaluation_returns_balanced_preset_values(tourn_root):
    plan = _write_plan(tourn_root, "", issue=866,
                       arms=[{"name": "balanced-only",
                              "env": {"BOT_QUOTE_TOURNAMENT_PRESET": "balanced"}}])
    result = arm_params.evaluate_arm(plan, "balanced-only", timeout=60.0)
    assert result.core.bot_quote_spread_offset_max_cents == 4
    assert result.core.bot_quote_spread_offset_min_cents == 2
    assert result.scoring_error is None


def test_flow_window_override_is_ignored_by_engine(tourn_root):
    plan = _write_plan(tourn_root, "", issue=866,
                       arms=[{"name": "flow5",
                              "env": {"BOT_ORDER_QUEUE_FLOW_WINDOW_SEC": "300"}}])
    result = arm_params.evaluate_arm(plan, "flow5", timeout=60.0)
    assert result.core.bot_order_queue_flow_window_sec == 1800.0


def test_memo_invalidates_on_plan_change(tourn_root):
    plan = _write_plan(tourn_root, "", issue=866,
                       arms=[{"name": "balanced-only",
                              "env": {"BOT_QUOTE_TOURNAMENT_PRESET": "balanced"}}])
    first = arm_params.evaluate_arm(plan, "balanced-only", timeout=60.0)
    assert first.core.bot_quote_spread_offset_max_cents == 4
    # Rewrite the plan with a different override and a newer mtime.
    plan.write_text(json.dumps({
        "issue": 866, "stamp": "20261030-120000", "run_number": 1,
        "minutes": 60.0,
        "arms": [{"name": "balanced-only",
                  "env": {"BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "7"}}],
    }), encoding="utf-8")
    second = arm_params.evaluate_arm(plan, "balanced-only", timeout=60.0)
    assert second.core.bot_quote_spread_offset_max_cents == 7

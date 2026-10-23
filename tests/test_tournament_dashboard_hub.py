"""Tests for the dashboard hub plan lookup (one grammar, one ranker)."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.tournament_dashboard_hub import get_latest_plan


def _plan(tourn_dir: Path, run_dir: str, payload: dict) -> None:
    folder = tourn_dir / run_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plan.json").write_text(json.dumps(payload), encoding="utf-8")


def _legacy(tourn_dir: Path, filename: str, payload: dict) -> None:
    (tourn_dir / filename).write_text(json.dumps(payload), encoding="utf-8")


def test_the_highest_run_wins(tmp_path: Path):
    _plan(tmp_path, "i492-r1_2026-10-17T18-48", {"issue": 492, "run_number": 1})
    _plan(tmp_path, "i492-r2_2026-10-18T18-48", {"issue": 492, "run_number": 2})
    plan = get_latest_plan("492", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["run_number"] == 2


def test_a_pre_folder_record_is_ignored(tmp_path: Path):
    """Only a run folder is a plan; a flat file in the root is not one.

    `tournament_i492-r1_<stamp>.json`, `i492_tournament-2.json` and
    `492_20261011-231751.json` were the previous generations. None is written
    any more, and reading one would resurrect a run whose store the refactor
    moved into a folder.
    """
    _plan(tmp_path, "i492-r1_2026-10-17T18-48", {"issue": 492, "run_number": 1})
    _legacy(tmp_path, "tournament_i492-r1_2026-10-18T18-48.json", {"issue": 492, "run_number": 9})
    _legacy(tmp_path, "i492_tournament-2.json", {"issue": 492, "run_number": 9})
    _legacy(tmp_path, "492_20261011-231751.json", {"issue": 492, "stamp": "old"})
    plan = get_latest_plan("492", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["run_number"] == 1


def test_a_named_run_resolves_without_a_run_number(tmp_path: Path):
    _plan(
        tmp_path,
        "i487-current-setup-10arm_2026-10-21T09-00",
        {"issue": 487, "stamp": "current_setup_10arm"},
    )
    plan = get_latest_plan("487", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["stamp"] == "current_setup_10arm"


def test_get_latest_plan_v2_single(tmp_path: Path):
    _plan(tmp_path, "i492-r1_2026-10-17T18-48", {"issue": 492, "run_number": 1})
    plan = get_latest_plan("492", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["issue"] == 492


def test_the_newest_stamp_wins_within_one_identity(tmp_path: Path):
    _plan(tmp_path, "i487_2026-10-11T22-44", {"issue": 487, "stamp": "a"})
    _plan(tmp_path, "i487_2026-10-11T23-17", {"issue": 487, "stamp": "b"})
    plan = get_latest_plan("487", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["stamp"] == "b"


def test_get_latest_plan_none_when_missing(tmp_path: Path):
    assert get_latest_plan("492", tourn_dir=tmp_path) is None

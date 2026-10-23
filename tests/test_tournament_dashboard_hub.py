"""Tests for the dashboard hub plan lookup (one grammar, one ranker)."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.tournament_dashboard_hub import get_latest_plan


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_the_highest_run_wins(tmp_path: Path):
    _write(tmp_path / "tournament_i492-r1_2026-10-17T18-48.json", {"issue": 492, "run_number": 1})
    _write(tmp_path / "tournament_i492-r2_2026-10-18T18-48.json", {"issue": 492, "run_number": 2})
    plan = get_latest_plan("492", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["run_number"] == 2


def test_a_pre_rename_record_is_ignored(tmp_path: Path):
    """The migration renamed every record; an old-style file is not a plan.

    `i492_tournament-2.json` and `492_20261011-231751.json` were the previous two
    generations. Neither is written any more, and reading one would resurrect a
    run whose store the migration renamed away.
    """
    _write(tmp_path / "tournament_i492-r1_2026-10-17T18-48.json", {"issue": 492, "run_number": 1})
    _write(tmp_path / "i492_tournament-2.json", {"issue": 492, "run_number": 2})
    _write(tmp_path / "492_20261011-231751.json", {"issue": 492, "stamp": "old"})
    plan = get_latest_plan("492", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["run_number"] == 1


def test_a_named_run_resolves_without_a_run_number(tmp_path: Path):
    _write(
        tmp_path / "tournament_i487-current-setup-10arm_2026-10-21T09-00.json",
        {"issue": 487, "stamp": "current_setup_10arm"},
    )
    plan = get_latest_plan("487", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["stamp"] == "current_setup_10arm"


def test_get_latest_plan_v2_single(tmp_path: Path):
    _write(tmp_path / "tournament_i492-r1_2026-10-17T18-48.json", {"issue": 492, "run_number": 1})
    plan = get_latest_plan("492", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["issue"] == 492


def test_the_newest_stamp_wins_within_one_identity(tmp_path: Path):
    _write(tmp_path / "tournament_i487_2026-10-11T22-44.json", {"issue": 487, "stamp": "a"})
    _write(tmp_path / "tournament_i487_2026-10-11T23-17.json", {"issue": 487, "stamp": "b"})
    plan = get_latest_plan("487", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["stamp"] == "b"


def test_get_latest_plan_none_when_missing(tmp_path: Path):
    assert get_latest_plan("492", tourn_dir=tmp_path) is None

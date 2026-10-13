"""Tests for the dashboard hub plan lookup (legacy + run-number schemes)."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.tournament_dashboard_hub import get_latest_plan


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_get_latest_plan_prefers_v2(tmp_path: Path):
    _write(tmp_path / "i492_tournament-1.json", {"issue": 492, "run_number": 1})
    _write(tmp_path / "i492_tournament-2.json", {"issue": 492, "run_number": 2})
    # A legacy stamped plan for the same issue must not shadow the v2 plan.
    _write(tmp_path / "492_20261011-231751.json", {"issue": 492, "stamp": "old"})
    plan = get_latest_plan("492", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["run_number"] == 2


def test_get_latest_plan_v2_single(tmp_path: Path):
    _write(tmp_path / "i492_tournament-1.json", {"issue": 492, "run_number": 1})
    plan = get_latest_plan("492", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["issue"] == 492


def test_get_latest_plan_legacy_unchanged(tmp_path: Path):
    _write(tmp_path / "487_20261011-224447.json", {"issue": 487, "stamp": "a"})
    _write(tmp_path / "487_20261011-231751.json", {"issue": 487, "stamp": "b"})
    plan = get_latest_plan("487", tourn_dir=tmp_path)
    assert plan is not None
    assert plan["stamp"] == "b"


def test_get_latest_plan_none_when_missing(tmp_path: Path):
    assert get_latest_plan("492", tourn_dir=tmp_path) is None

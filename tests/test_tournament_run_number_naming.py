"""Tests for the run-number tournament naming scheme (Issue #492).

Scheme: slugs become <base>-<run>, DBs become
data/i<issue>_tournament-<run>_<slug>.db, run ids become
shadow-<issue>-tournament-<run>-<slug>, plan files become
runtime/tournaments/i<issue>_tournament-<run>.json. No index prefix, no stamp.
The legacy builders are untouched (see test_shadow_tournament.py).
"""
from __future__ import annotations

from pathlib import Path

from core_brain.shadow_run import (
    build_tournament_db_path_v2,
    build_tournament_log_path,
    build_tournament_log_path_v2,
    build_tournament_run_id_v2,
    parse_tournament_db_path,
)


def test_v2_db_path_shape():
    assert build_tournament_db_path_v2(
        issue=492, arm="control-1", run_number=1, base_dir="data"
    ) == Path("data/i492_tournament-1_control-1.db")
    assert build_tournament_db_path_v2(
        issue=492, arm="deadband-tight-1", run_number=2, base_dir="data"
    ) == Path("data/i492_tournament-2_deadband-tight-1.db")


def test_v2_log_path_shape():
    # Detached-launch stdout redirect lives next to the plan record under
    # runtime/tournaments/ -- never logs/ (which does not exist in this repo).
    assert build_tournament_log_path_v2(issue=492, run_number=1) == Path(
        "runtime/tournaments/i492_tournament-1.log"
    )
    assert build_tournament_log_path_v2(issue=492, run_number=2) == Path(
        "runtime/tournaments/i492_tournament-2.log"
    )
    assert build_tournament_log_path(
        issue=371, stamp="20261005-032000"
    ) == Path("runtime/tournaments/371_20261005-032000.log")


def test_v2_run_id_shape():
    run_id = build_tournament_run_id_v2(issue=492, arm="conservative-1", run_number=1)
    assert run_id == "shadow-492-tournament-1-conservative-1"
    assert len(run_id) <= 64
    assert run_id.startswith("shadow-")


def test_parse_v2_db_path():
    parsed = parse_tournament_db_path("data/i492_tournament-1_control-1.db")
    assert parsed == {
        "issue": 492,
        "arm": "control-1",
        "run_number": 1,
        "index": None,
        "stamp": None,
    }


def test_parse_still_rejects_non_tournament():
    assert parse_tournament_db_path("data/orders.db") is None
    assert parse_tournament_db_path("data/NN_shadow_20261004.db") is None
    assert parse_tournament_db_path("") is None
    assert parse_tournament_db_path(None) is None


def test_legacy_shape_still_parses():
    # Legacy shape needs a timestamp stamp (custom stamps like the first-run
    # '12hr' never matched the parser, before or after this change).
    parsed = parse_tournament_db_path("data/484_tournament_01_control_20261011-120000.db")
    assert parsed is not None
    assert parsed["issue"] == 484
    assert parsed["arm"] == "control"
    assert parsed["index"] == 1


def test_build_plan_run_number_scheme(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    arms = [{"name": "baseline"}, {"name": "conservative"}]
    plan = build_tournament_plan(
        issue=492,
        arms=arms,
        base_port=8801,
        base_dir=tmp_path,
        check_ports=False,
        run_number=1,
    )
    assert plan.run_number == 1
    assert [a.name for a in plan.arms] == ["baseline-1", "conservative-1"]
    assert plan.arms[0].db_path.name == "i492_tournament-1_baseline-1.db"
    assert plan.arms[0].run_id == "shadow-492-tournament-1-baseline-1"
    assert plan.arms[1].db_path.name == "i492_tournament-1_conservative-1.db"
    assert "tournament-1" in plan.record_path.name
    assert plan.record_path.name == "i492_tournament-1.json"
    assert plan.results_path.name == "i492_tournament-1_results.json"
    assert plan.log_path is not None
    assert plan.log_path.name == "i492_tournament-1.log"
    assert plan.log_path.parent.name == "tournaments"
    assert plan.to_dict()["log_path"] == str(plan.log_path)
    assert "stamp" not in plan.arms[0].db_path.name
    # Base name still drives the preset selector, not the suffixed slug.
    assert plan.arms[0].env["HUNTER_TOURNAMENT_PRESET"] == "baseline"


def test_build_plan_run_number_two(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=492,
        arms=[{"name": "snapstop"}],
        base_port=8801,
        base_dir=tmp_path,
        check_ports=False,
        run_number=2,
    )
    assert plan.arms[0].name == "snapstop-2"
    assert plan.arms[0].db_path.name == "i492_tournament-2_snapstop-2.db"
    assert plan.record_path.name == "i492_tournament-2.json"
    assert plan.log_path is not None
    assert plan.log_path.name == "i492_tournament-2.log"


def test_build_plan_legacy_default_unchanged(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=371,
        base_port=8801,
        base_dir=tmp_path,
        stamp="20261005-032000",
        check_ports=False,
    )
    assert plan.run_number is None
    assert [a.name for a in plan.arms][:2] == ["conservative", "balanced"]
    assert plan.arms[0].db_path.name.startswith("371_tournament_01_")
    assert "20261005-032000" in plan.arms[0].db_path.name

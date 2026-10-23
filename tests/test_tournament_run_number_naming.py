"""Tournament artifact naming (docs/agents/naming.md, grammar G3).

One grammar covers every tournament file, in every directory:

    tournament_<key>_<stamp>.json          plan record      runtime/tournaments/
    tournament_<key>_<stamp>.log           launcher log     runtime/tournaments/
    tournament_results_<key>_<stamp>.json  results          reports/tournaments/
    tournament_<key>_<arm>.db              store            data/
    <key>_arms.json                        arm list (input) configs/tournaments/

`<key>` is `i<issue>-r<run>` for a numbered run, else `i<issue>` or
`i<issue>-<name>` from `--stamp`. The stamp is always the ISO launch stamp.

Before this, one directory held four generations -- `i492_tournament-1.json`,
`371_20261005-032000.json`, `487_current_setup_10arm.json`, and results with a
reversed suffix -- and two readers each re-derived "the latest plan" with their
own regex.
"""
from __future__ import annotations

from pathlib import Path

from core_brain.shadow_run import (
    build_tournament_db_path_v2,
    build_tournament_log_path,
    build_tournament_record_path,
    build_tournament_results_path,
    build_tournament_run_id_v2,
    latest_tournament_record,
    parse_tournament_db_path,
    parse_tournament_record_name,
    tournament_key,
)

STAMP = "2026-10-05T03-20"


def test_key_shapes():
    assert tournament_key(492, run_number=1) == "i492-r1"
    assert tournament_key(487, name="current_setup_10arm") == "i487-current-setup-10arm"
    assert tournament_key(371) == "i371"


def test_v2_db_path_shape():
    assert build_tournament_db_path_v2(
        issue=492, arm="control-1", run_number=1, base_dir="data"
    ) == Path("data/tournament_i492-r1_control-1.db")
    assert build_tournament_db_path_v2(
        issue=492, arm="deadband-tight-1", run_number=2, base_dir="data"
    ) == Path("data/tournament_i492-r2_deadband-tight-1.db")


def test_record_results_and_log_share_one_stem():
    record = build_tournament_record_path(492, STAMP, run_number=1)
    log = build_tournament_log_path(492, STAMP, run_number=1)
    results = build_tournament_results_path(492, STAMP, run_number=1)

    assert record.parent.name == "tournaments" and record.parent.parent.name == "runtime"
    assert record.name == f"tournament_i492-r1_{STAMP}.json"
    # Same stem as the record, different extension: the detached-launch stdout
    # redirect sits next to the plan it executed.
    assert log.name == f"tournament_i492-r1_{STAMP}.log"
    assert record.stem == log.stem
    assert results.parent.parent.name == "reports"
    assert results.name == f"tournament_results_i492-r1_{STAMP}.json"


def test_a_named_run_still_carries_the_iso_stamp():
    # `--stamp current_setup_10arm` names the run; it does not replace the
    # timestamp, so the record sorts and can never collide with a repeat.
    record = build_tournament_record_path(487, STAMP, name="current_setup_10arm")
    assert record.name == f"tournament_i487-current-setup-10arm_{STAMP}.json"


def test_v2_run_id_shape():
    run_id = build_tournament_run_id_v2(issue=492, arm="conservative-1", run_number=1)
    assert run_id == "shadow-492-tournament-1-conservative-1"
    assert len(run_id) <= 64
    assert run_id.startswith("shadow-")


def test_parse_v2_db_path():
    parsed = parse_tournament_db_path("data/tournament_i492-r1_control-1.db")
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
    # Files already on disk keep parsing: the legacy spelling stays in the
    # pattern, and the ISO spelling is accepted for the same shape.
    parsed = parse_tournament_db_path("data/484_tournament_01_control_20261011-120000.db")
    assert parsed is not None
    assert parsed["issue"] == 484
    assert parsed["arm"] == "control"
    assert parsed["index"] == 1

    current = parse_tournament_db_path("data/484_tournament_01_control_2026-10-11T12-00.db")
    assert current is not None
    assert current["stamp"] == "2026-10-11T12-00"


def test_parse_record_name():
    assert parse_tournament_record_name("tournament_i608-r2_2026-10-17T18-48.json") == {
        "issue": 608,
        "run_number": 2,
        "name": None,
        "stamp": "2026-10-17T18-48",
    }
    assert parse_tournament_record_name(
        "tournament_i487-current-setup-10arm_2026-10-01T09-00.json"
    ) == {
        "issue": 487,
        "run_number": None,
        "name": "current-setup-10arm",
        "stamp": "2026-10-01T09-00",
    }
    # Not a plan record: results, a store, and the pre-rename generations.
    assert parse_tournament_record_name("tournament_results_i608-r2_2026-10-17T18-48.json") is None
    assert parse_tournament_record_name("tournament_i608-r2_the-surgeon-2.db") is None
    assert parse_tournament_record_name("i608_tournament-2.json") is None
    assert parse_tournament_record_name("608_20261017-184837.json") is None


def test_latest_record_ranks_on_fields_not_on_the_filename(tmp_path: Path):
    """Highest run wins, then newest stamp -- never string order.

    The old ranked-by-string readers picked a named record last only because
    `c` sorts after a digit, and would have picked run 10 before run 9.
    """
    for name in (
        "tournament_i608-r1_2026-10-17T18-48.json",
        "tournament_i608-r9_2026-10-18T09-00.json",
        "tournament_i608-r10_2026-10-19T09-00.json",
        "tournament_i608-current-setup_2026-10-20T09-00.json",
        "tournament_i608-r10_2026-10-19T08-00.json",
        "tournament_results_i608-r11_2026-10-21T09-00.json",  # not a plan record
        "tournament_i609-r99_2026-10-21T09-00.json",  # another issue
    ):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    latest = latest_tournament_record(608, tmp_path)
    assert latest is not None
    # Run 10 beats run 9 and the unnamed-run record; the later of the two run-10
    # records wins on stamp. The results file and issue 609 are ignored.
    assert latest.name == "tournament_i608-r10_2026-10-19T09-00.json"


def test_latest_record_ignores_a_longer_issue_number(tmp_path: Path):
    """`tournament_i60*` also matches `i608`, so the parsed id has to decide.

    Without the check the higher run number of the *other* issue wins, and the
    cockpit reads a plan that belongs to a different tournament.
    """
    (tmp_path / "tournament_i608-r9_2026-10-19T09-00.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tournament_i60-r1_2026-10-01T09-00.json").write_text("{}", encoding="utf-8")

    latest = latest_tournament_record(60, tmp_path)
    assert latest is not None
    assert latest.name == "tournament_i60-r1_2026-10-01T09-00.json"


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
    assert plan.arms[0].db_path.name == "tournament_i492-r1_baseline-1.db"
    assert plan.arms[0].run_id == "shadow-492-tournament-1-baseline-1"
    assert plan.arms[1].db_path.name == "tournament_i492-r1_conservative-1.db"
    assert plan.record_path.name == f"tournament_i492-r1_{plan.stamp}.json"
    assert plan.results_path.name == f"tournament_results_i492-r1_{plan.stamp}.json"
    assert plan.log_path is not None
    assert plan.log_path.name == f"tournament_i492-r1_{plan.stamp}.log"
    assert plan.log_path.parent.name == "tournaments"
    assert plan.log_path.parent.parent.name == "runtime"
    assert plan.to_dict()["log_path"] == str(plan.log_path)
    # The store drops the stamp (the run key is already unique); every other
    # artifact carries it.
    assert plan.stamp not in plan.arms[0].db_path.name
    assert plan.record_path.is_absolute()
    # Base name still drives the preset selector, not the suffixed slug.
    assert plan.arms[0].env["BOT_QUOTE_TOURNAMENT_PRESET"] == "baseline"


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
    assert plan.arms[0].db_path.name == "tournament_i492-r2_snapstop-2.db"
    assert plan.record_path.name == f"tournament_i492-r2_{plan.stamp}.json"
    assert plan.log_path is not None
    assert plan.log_path.name == f"tournament_i492-r2_{plan.stamp}.log"


def test_build_plan_legacy_branch_keeps_its_store_shape(tmp_path: Path):
    """No run number: the legacy indexed store survives, the artifacts do not.

    The store shape is load-bearing for the dashboard's clustering parser (it
    reads the arm index), so it stays. Everything else follows the one grammar.
    """
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=371,
        base_port=8801,
        base_dir=tmp_path,
        stamp=STAMP,
        check_ports=False,
    )
    assert plan.run_number is None
    assert [a.name for a in plan.arms][:2] == ["conservative", "balanced"]
    assert plan.arms[0].db_path.name == f"371_tournament_01_conservative_{STAMP}.db"
    assert plan.record_path.name == f"tournament_i371_{STAMP}.json"
    assert plan.results_path.name == f"tournament_results_i371_{STAMP}.json"


def test_build_plan_named_run(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=487,
        base_port=8801,
        base_dir=tmp_path,
        stamp=STAMP,
        run_name="current_setup_10arm",
        check_ports=False,
    )
    assert plan.record_path.name == f"tournament_i487-current-setup-10arm_{STAMP}.json"
    assert plan.results_path.name == f"tournament_results_i487-current-setup-10arm_{STAMP}.json"
